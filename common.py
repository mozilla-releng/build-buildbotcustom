def getSupportedPlatforms():
    return ('linux', 'linux64', 'win32', 'win64', 'wince', 'macosx', 'macosx64', 'android')

def getPlatformFtpDir(platform):
    platform_ftp_map = {
        'linux': 'linux-i686',
        'linux64': 'linux-x86_64',
        'macosx': 'mac',
        'macosx64': 'mac64',
        'win32': 'win32',
        'win64': 'win64',
        'wince': 'wince-arm',
        'android': 'android-r7',
    }
    return platform_ftp_map.get(platform)
