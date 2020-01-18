import asyncio
import copy
import glob
import os
import os.path
from typing import List
import json

import aiohttp
import discord
# from discord.ext import commands
from redbot.core import commands
from redbot.core.utils import chat_formatting as cf
from redbot.core import Config

class SuspendedPlayer:

    def __init__(self, voice_client):
        self.vchan = voice_client.channel
        self.vc_ap = voice_client.audio_player

class Sfx(commands.Cog):
    """Inject sound effects into a voice channel"""

    def __init__(self, bot):
        self.bot = bot
        self.audio_players = {}
        self.config = Config.get_conf(self, identifier=1234567890)
        self.settings = {}
        self.sound_base = os.path.dirname(str(self.config.driver.data_path))
        self.SetupFolderFiles()
        self.default_volume = 75
        self.vc_buffers = {}
        self.master_queue = asyncio.Queue()
        # Combine slave_queues and slave_tasks into a single dict, maybe
        self.slave_queues = {}
        self.slave_tasks = {}
        self.queue_task = bot.loop.create_task(self._queue_manager())
        self.session = aiohttp.ClientSession()
        
    def SetupFolderFiles(self):
        self.temp_filepath = self.sound_base + "/temp/"
        self.settings_path = self.sound_base + "/settings.json"
        # Create Folders
        if not os.path.exists(self.temp_filepath):
            print("Creating {} folder...".format(self.temp_filepath))
            os.makedirs(self.temp_filepath)
        files = glob.glob(self.temp_filepath + '/*')
        for f in files:
            try:
                os.remove(f)
            except PermissionError:
                print("Could not delete file '{}'. "
                      "Check your file permissions.".format(f))
        # # Create Files
        # f = self.settings_path
        # if not dataIO.is_valid_json(f):
            # print("Creating data/playsound/settings.json...")
            # self.save_json(f, {})
        
    async def load(self):
        await self.load_json(self.settings_path)
        
    async def load_json(self,settings_path):
        with open(settings_path) as json_file:
            self.settings = json.load(json_file)
        
    async def save_json(self,path,data):
        with open(path, 'w') as outfile:
            json.dump(data, outfile)
        
    def __unload(self):
        self.session.close()

    # Other cogs may use the following two functions as an easy API for sfx.
    # The function definitions probably violate every possible style guide,
    # But I like it :)
    def enqueue_sfx(self, vchan: discord.VoiceChannel,
                           path: str,
                            vol: int=None,
                       priority: int=5,
                         delete: bool=False,
                          tchan: discord.VoiceChannel=None):
        if vol is None:
            vol = self.default_volume
        try:
            item = {'cid': vchan.id, 'path': path, 'vol': vol,
                    'priority': priority, 'delete': delete, 'tchan': tchan}
            self.master_queue.put_nowait(item)
            return True
        except asyncio.QueueFull:
            return False

    def _list_sounds(self, server_id: str) -> List[str]:
        return sorted(
            [os.path.splitext(s)[0] for s in os.listdir(os.path.join(
                self.sound_base, server_id))],
            key=lambda s: s.lower())

    async def _change_and_resume(self, vc, channel: discord.VoiceChannel):
        await vc.move_to(channel)
        vc.audio_player.resume()

    def _revive_audio(self, sid):
        server = self.bot.get_server(sid)
        vc_current = self.bot.voice_client_in(server)
        vc_current.audio_player = self.vc_buffers[sid].vc_ap
        vchan_old = self.vc_buffers[sid].vchan
        self.vc_buffers[sid] = None
        if vc_current.channel.id != vchan_old.id:
            self.bot.loop.create_task(self._change_and_resume(vc_current,
                                                              vchan_old))
        else:
            vc_current.audio_player.resume()

    def _suspend_audio(self, vc, cid):
        channel = self.bot.get_channel(cid)
        vc.audio_player.pause()
        self.vc_buffers[channel.server.id] = SuspendedPlayer(vc)

    async def _slave_queue_manager(self, queue, sid):
        server = self.bot.get_guild(int(sid))
        timeout_counter = 0
        audio_cog = self.bot.get_cog('Audio')
        while True:
            await asyncio.sleep(0.1)
            try:
                next_sound = queue.get_nowait()
                # New sound was found, restart the timer!
                timeout_counter = 0
            except asyncio.QueueEmpty:
                timeout_counter += 1
                # give back control to any prior voice clients
                if self.vc_buffers.get(sid) is not None:
                    self._revive_audio(sid)
                vc = server.voice_client
                if vc is not None:
                    if (hasattr(vc, 'audio_player') and
                            vc.audio_player.is_playing()):
                        # This should not happen unless some other cog has
                        # stolen voice client so its safe to kill the task
                        return
                else:
                    # Something else killed our voice client, 
                    # so its also safe to kill the task
                    return
                # If we're here, we still have control of the voice client,
                # So it's our job to wait for disconnect
                timeout_counter += 1
                if timeout_counter > 10:
                    #await vc.disconnect()
                    return
                continue

            # This function can block itself from here on out. Our only job
            # is to play the sound and watch for Audio stealing back control
            await audio_cog._manager.shutdown()
            cid = next_sound['cid']
            channel = self.bot.get_channel(cid)
            path = next_sound['path']
            vol = next_sound['vol']
            delete = next_sound['delete']
            pitch = next_sound['priority']
            vc = server.voice_client
            
            options = "-filter \"volume=volume={}\"".format(str(vol/100))
            if pitch != "1":
                try:
                    float(pitch)
                except ValueError:
                    continue
                print('Custom pitch with 32000 as default bitrate')
                options += " -filter:a \"asetrate={}\"".format(float(pitch)*32000)
            
            if vc is None:
                # Voice not in use, we can connect to a voice channel
                try:
                    vc = await channel.connect()
                except asyncio.TimeoutError:
                    print("Could not join channel '{}'".format(channel.name))
                    if delete:
                        os.remove(path)
                    continue

                self.audio_players[sid] = discord.FFmpegPCMAudio( 
                    path, options=options)
                vc.play(self.audio_players[sid])
            else:
                # We already have a client, use it
                if (hasattr(vc, 'audio_player') and
                        vc.audio_player.is_playing()):
                    self._suspend_audio(vc, cid)

                if vc.channel.id != cid:
                    # It looks like this does not raise an exception if bot
                    # fails to join channel. Need to add a manual check.
                    await vc.move_to(channel)
                
                self.audio_players[sid] = discord.FFmpegPCMAudio( 
                    path, options=options)
                vc.play(self.audio_players[sid])

            # Wait for current sound to finish playing
            # Watch for audio interrupts
            while vc.is_playing():
                await asyncio.sleep(0.1)
                # if audio_cog is not None:
                # audio_cog.voice_client(server)
                # if vc is not None:
                    # if (hasattr(vc, 'audio_player') and
                            # vc.audio_player.is_playing()):
                        # # We were interrupted, how rude :c
                        # # Let's be polite and destroy our queue and go home.
                        # self.audio_players[sid].stop()
                        # return
            #if queue.empty():
                #await audio_cog.attempt_connect()
            if delete:
                os.remove(path)

    @commands.command(no_pm=True, pass_context=True)
    async def sfx(self, ctx, soundname: str, pitch : str = "1"):
        """Plays the specified sound."""
        
        server = ctx.message.guild
        serverid = str(server.id)
        vchan = ctx.voice_client
        for s in server.voice_channels:
            if ctx.author in s.members:
                vchan = s

        if vchan is None:
            await ctx.send("You are not connected to a voice channel.")
            return

        if serverid not in os.listdir(self.sound_base):
            os.makedirs(os.path.join(self.sound_base, serverid))

        if serverid not in self.settings:
            self.settings[serverid] = {}
            await self.save_json(self.settings_path, self.settings)

        f = glob.glob(os.path.join(
            self.sound_base, serverid, soundname + ".*"))

        if len(f) < 1:
            # No exact match, but try for a partial match
            f = glob.glob(os.path.join(self.sound_base, serverid, soundname + "*"))
            await ctx.send(cf.error(
                "Sound file not found. Try `{}allsfx` for a list.".format(
                    ctx.prefix)))
            return

        elif len(f) > 1:
            # There are identical file names, so this is still a valid error
            await ctx.send(cf.error(
                "There are {} sound files with the same name, but different"
                " extensions, and I can't deal with it. Please make filenames"
                " (excluding extensions) unique.".format(len(f))))
            return

        soundname = os.path.splitext(os.path.basename(f[0]))[0]
        if soundname in self.settings[serverid]:
            if "volume" in self.settings[serverid][soundname]:
                vol = self.settings[serverid][soundname]["volume"]
            else:
                vol = self.default_volume
                self.settings[serverid][soundname]["volume"] = vol
                await self.save_json(self.settings_path, self.settings)
        else:
            vol = self.default_volume
            self.settings[serverid][soundname] = {"volume": vol}
            await self.save_json(self.settings_path, self.settings)

        self.enqueue_sfx(vchan, f[0], vol=vol,priority=pitch)

    @commands.command(pass_context=True)
    async def allsfx(self, ctx):
        """Sends a list of every sound in a PM."""

        # await self.bot.type()

        server = ctx.message.guild
        serverid = str(server.id)

        if serverid not in os.listdir(self.sound_base):
            os.makedirs(os.path.join(self.sound_base, serverid))

        if serverid not in self.settings:
            self.settings[serverid] = {}
            self.save_json(self.settings_path, self.settings)

        strbuffer = self._list_sounds(serverid)

        if len(strbuffer) == 0:
            await ctx.send(cf.warning(
                "No sounds found. Use `{}addsfx` to add one.".format(
                    ctx.prefix)))
            return

        mess = "```"
        for line in strbuffer:
            if len(mess) + len(line) + 4 < 2000:
                mess += "\n" + line
            else:
                mess += "```"
                await ctx.send(mess)
                mess = "```" + line
        if mess != "":
            mess += "```"
            await ctx.send(mess)

    @commands.command(no_pm=True, pass_context=True)
    async def addsfx(self, ctx, link: str=None):
        """Adds a new sound.

        Either upload the file as a Discord attachment and make your comment
        "[p]addsfx", or use "[p]addsfx direct-URL-to-file".
        """

        # await self.bot.type()

        server = ctx.message.guild
        serverid = str(server.id)

        if serverid not in os.listdir(self.sound_base):
            os.makedirs(os.path.join(self.sound_base, serverid))

        if server.id not in self.settings:
            self.settings[server.id] = {}
            await self.save_json(self.settings_path, self.settings)

        attach = ctx.message.attachments
        if len(attach) > 1 or (attach and link):
            await ctx.send(
                cf.error("Please only add one sound at a time."))
            return

        url = ""
        filename = ""
        if attach:
            a = attach[0]
            url = a.url
            filename = a.filename
        elif link:
            url = "".join(link)
            filename = os.path.basename(
                "_".join(url.split()).replace("%20", "_"))
        else:
            await ctx.send(
                cf.error("You must provide either a Discord attachment or a"
                         " direct link to a sound."))
            return

        filepath = os.path.join(self.sound_base, serverid, filename)

        if os.path.splitext(filename)[0] in self._list_sounds(serverid):
            await ctx.send(
                cf.error("A sound with that filename already exists."
                         " Please change the filename and try again."))
            return

        async with self.session.get(url) as new_sound:
            f = open(filepath, "wb")
            f.write(await new_sound.read())
            f.close()

        self.settings[server.id][
            os.path.splitext(filename)[0]] = {"volume": self.default_volume}
        await self.save_json(self.settings_path, self.settings)

        await ctx.send(
            cf.info("Sound {} added.".format(os.path.splitext(filename)[0])))

    @commands.command(no_pm=True, pass_context=True)
    async def sfxvol(self, ctx, soundname: str, percent: int=None):
        """Sets the volume for the specified sound.

        If no value is given, the current volume for the sound is printed.
        """

        # await self.bot.type()

        server = ctx.message.guild
        serverid = str(server.id)

        if serverid not in os.listdir(self.sound_base):
            os.makedirs(os.path.join(self.sound_base, serverid))

        if serverid not in self.settings:
            self.settings[serverid] = {}
            await self.save_json(self.settings_path, self.settings)

        f = glob.glob(os.path.join(self.sound_base, serverid,
                                   soundname + ".*"))
        if len(f) < 1:
            await ctx.send(cf.error(
                "Sound file not found. Try `{}allsfx` for a list.".format(
                    ctx.prefix)))
            return
        elif len(f) > 1:
            await ctx.send(cf.error(
                "There are {} sound files with the same name, but different"
                " extensions, and I can't deal with it. Please make filenames"
                " (excluding extensions) unique.".format(len(f))))
            return

        if soundname not in self.settings[serverid]:
            self.settings[serverid][soundname] = {"volume": self.default_volume}
            await self.save_json(self.settings_path, self.settings)

        if percent is None:
            await ctx.send("Volume for {} is {}.".format(
                soundname, self.settings[serverid][soundname]["volume"]))
            return

        self.settings[serverid][soundname]["volume"] = percent
        await self.save_json(self.settings_path, self.settings)

        await ctx.send("Volume for {} set to {}.".format(soundname,
                                                               percent))

    @commands.command(no_pm=True, pass_context=True)
    async def delsfx(self, ctx, soundname: str):
        """Deletes an existing sound."""

        # await self.bot.type()

        server = ctx.message.guild
        serverid = str(server.id)

        if serverid not in os.listdir(self.sound_base):
            os.makedirs(os.path.join(self.sound_base, serverid))

        f = glob.glob(os.path.join(self.sound_base, serverid,
                                   soundname + ".*"))
        if len(f) < 1:
            await ctx.send(cf.error(
                "Sound file not found. Try `{}allsfx` for a list.".format(
                    ctx.prefix)))
            return
        elif len(f) > 1:
            await ctx.send(cf.error(
                "There are {} sound files with the same name, but different"
                " extensions, and I can't deal with it. Please make filenames"
                " (excluding extensions) unique.".format(len(f))))
            return

        os.remove(f[0])

        if soundname in self.settings[serverid]:
            del self.settings[serverid][soundname]
            await self.save_json(self.settings_path, self.settings)

        await ctx.send(cf.info("Sound {} deleted.".format(soundname)))

    async def _queue_manager(self):
        await self.bot.wait_until_ready()
        while True:
            await asyncio.sleep(0.1)
            # First check for empty queues
            for slave in self.slave_tasks:
                if (self.slave_tasks[slave] is not None and
                        self.slave_tasks[slave].done()):
                    # Task is not completed until:
                    # Slave queue is empty, and timeout is reached /
                    # vc disconnected / someone else stole vc
                    self.slave_tasks[slave] = None
                    self.slave_queues[slave] = None

            # Next we can check for new items
            item = None
            try:
                item = self.master_queue.get_nowait()
            except asyncio.QueueEmpty:
                continue
            # This does not really check to make sure the queued item
            # is valid. Should probably check that with the enqueue function.
            channel = self.bot.get_channel(item['cid'])
            server = channel.guild
            sid = str(server.id)
            priority = item['priority']

            if self.slave_tasks.get(sid) is None:
                # Create slave queue
                queue = asyncio.Queue(maxsize=20)
                self.slave_queues[sid] = queue
                self.slave_tasks[sid] = self.bot.loop.create_task(
                                            self._slave_queue_manager(queue,
                                                                        sid))
            try:
                self.slave_queues[sid].put_nowait(item)
            except asyncio.QueueFull:
                # It's possible to add a way to handle full queue situation.
                pass
        # Need to add cancelled task exception handler?

    def __unload(self):
        self.queue_task.cancel()
        for slave in self.slave_tasks:
            if self.slave_tasks[slave] is not None:
                self.slave_tasks[slave].cancel()
