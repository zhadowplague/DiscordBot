from .sfx import Sfx

async def setup(bot):
    sfx = Sfx(bot)
    await sfx.load()
    bot.add_cog(sfx)