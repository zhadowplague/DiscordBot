# DiscordBot
A modded version of flapjacks sfx cog for redbot v2:
https://github.com/flapjax/FlapJack-Cogs/blob/master/sfx/sfx.py

Note to self:
# To install run the following in a powershell with admin
Set-ExecutionPolicy Bypass -Scope Process -Force
iex ((New-Object System.Net.WebClient).DownloadString('https://chocolatey.org/install.ps1'))
choco install git --params "/GitOnlyOnPath /WindowsTerminal" -y
choco install visualstudio2019-workload-vctools -y
choco install python3 -y
choco install adoptopenjdk11jre -y
choco install ffmpeg

# And then in a cmd
python -m pip install -U pip setuptools wheel
python -m pip install -U Red-DiscordBot
