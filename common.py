import time, uuid

def getSupportedPlatforms():
    return ('linux', 'linux64', 'win32', 'win64', 'wince', 'macosx',
            'macosx64', 'android')

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

def genBuildID():
    """Return a buildid based on the current time"""
    now = time.time()
    return time.strftime("%Y%m%d%H%M%S", time.localtime(now))

def genBuildUID():
    """Return a unique build uid"""
    return uuid.uuid4().hex
