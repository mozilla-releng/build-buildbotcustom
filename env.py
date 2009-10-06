# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is Mozilla-specific Buildbot steps.
#
# The Initial Developer of the Original Code is
# Mozilla Corporation.
# Portions created by the Initial Developer are Copyright (C) 2007
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#   Ben Hearsum <bhearsum@mozilla.com>
#   Rob Campbell <rcampbell@mozilla.com>
#   Chris Cooper <ccooper@mozilla.com>
# ***** END LICENSE BLOCK *****

MozillaEnvironments = {}

MozillaEnvironments['win32-ref-platform'] = {
    "MOZ_TOOLS": 'd:\\mozilla-build\\moztools',
    "VSINSTALLDIR": 'D:\\msvs8',
    "VS80COMMTOOLS": 'D:\\msvs8\\Common7\\Tools\\',
    "VCINSTALLDIR": 'D:\\msvs8\\VC',
    "FrameworkDir": 'C:\\WINDOWS\\Microsoft.NET\\Framework',
    "FrameworkSDKDir": 'D:\\msvs8\\SDK\\v2.0',
    "DevEnvDir": "D:\\msvs8\\VC\\Common7\\IDE",
    "MSVCDir": 'D:\\msvs8\\VC',
    "PATH": 'd:\\sdks\\v6.0\\bin;' + \
            'D:\\msvs8\\Common7\\IDE;' + \
            'D:\\msvs8\\VC\\bin;' + \
            'C:\\msvs8\\SDK\\bin;' + \
            'D:\\msvs8\\VC;' + \
            'D:\\msvs8\\Common7\\Tools;' + \
            'D:\\msvs8\\Common7\\Tools\\bin;' + \
            'd:\\mozilla-build\\hg;' + \
            'd:\\mozilla-build\\moztools\\bin;' + \
            'd:\\mozilla-build\\msys\\local\\bin;' + \
            'd:\\mozilla-build\\msys\\bin;' + \
            'd:\\mozilla-build\\7zip;' + \
            'd:\\mozilla-build\\upx203w;' + \
            'd:\\mozilla-build\\python25;' + \
            'd:\\mozilla-build\\blat261\\full;' + \
            'd:\\mozilla-build\\info-zip;' + \
            'd:\\mozilla-build\\wget;' + \
            'd:\\mozilla-build\\nsis-2.22;',
            'D:\\mozilla-build\\nsis-2.33u;' + \
            'd:\\sdks\\v6.0\\bin'
    "INCLUDE": 'D:\\sdks\\v6.0\\include;' + \
               'D:\\sdks\\v6.0\\include\\atl;' + \
               'D:\\msvs8\\VC\\ATLMFC\\INCLUDE;' + \
               'D:\\msvs8\\VC\\INCLUDE;' + \
               'D:\\msvs8\\VC\\PlatformSDK\\include',
    "LIB": 'D:\\sdks\\v6.0\\lib;' + \
           'D:\\msvs8\\VC\\ATLMFC\\LIB;' + \
           'D:\\msvs8\\VC\\LIB;' + \
           'D:\\msvs8\\VC\\PlatformSDK\\lib',
    "SDKDIR": 'D:\\sdks\\v6.0'
}

### unittest environments

MozillaEnvironments['linux-unittest'] = {
    "MOZ_NO_REMOTE": '1',
    "CVS_RSH": 'ssh',
    "DISPLAY": ':2',
    "NO_FAIL_ON_TEST_ERRORS": '1'   
}

MozillaEnvironments['macosx-unittest'] = {
    "MOZ_NO_REMOTE": '1',
    "NO_EM_RESTART": '1',
    "XPCOM_DEBUG_BREAK": 'warn',
    "CVS_RSH": 'ssh',
    "NO_FAIL_ON_TEST_ERRORS": '1'  
}

MozillaEnvironments['win32-unittest'] = {
    "MOZ_NO_REMOTE": '1',
    "NO_EM_RESTART": '1',
    "MOZ_AIRBAG": '1',
    "MOZ_CRASHREPORTER_NO_REPORT": '1',
    "XPCOM_DEBUG_BREAK": 'warn',
    "VCVARS": 'D:\\msvs8\\VC\\bin\\vcvars32.bat',
    "MOZ_MSVCVERSION": '8',
    "MOZILLABUILD": 'D:\\mozilla-build',
    "MOZILLABUILDDRIVE": 'C:',
    "MOZILLABUILDPATH": '\\mozilla-build\\',
    "MOZ_TOOLS": 'D:\\mozilla-build\\moztools',
    "CVS_RSH": 'ssh',
    "NO_FAIL_ON_TEST_ERRORS": '1',
    "VSINSTALLDIR": 'D:\\msvs8',
    "VCINSTALLDIR": 'D:\\msvs8\\VC',
    "FrameworkDir": 'C:\\WINDOWS\\Microsoft.NET\\Framework',
    "FrameworkVersion": 'v2.0.50727',
    "FrameworkSDKDir": 'D:\\msvs8\\SDK\\v2.0',
    "MSVCDir": 'D:\\msvs8\\VC',
    "DevEnvDir": "D:\\msvs8\\Common7\\IDE",
    "LIBPATH": 'C:\\WINDOWS\\Microsoft.NET\\Framework\\v2.0.50727;' + \
               'D:\\msvs8\\VC\\ATLMFC\\LIB'
}

MozillaEnvironments['winmo-arm'] = {
    "DEVENVDIR": 'd:\\msvs9\\Common7\\IDE',
    "FRAMEWORK35VERSION": 'v3.5',
    "FRAMEWORKDIR": 'C:\\WINDOWS\\Microsoft.NET\\Framework',
    "FRAMEWORKVERSION": 'v2.0.50727',
    "LIBPATH": 'C:\\WINDOWS\\Microsoft.NET\\Framework\\v3.5;' + \
               'C:\\WINDOWS\\Microsoft.NET\\Framework\\v2.0.50727;' + \
               'd:\\msvs9\\VC\\ATLMFC\\LIB;d:\\msvs9\\VC\\LIB;',
    "MOZILLABUILD": 'D:\\mozilla-build\\',
    "MOZILLABUILDDRIVE": 'D:',
    "MOZILLABUILDPATH": '\\mozilla-build\\',
    "MOZ_MSVCVERSION": '9',
    "MOZ_NO_RESET_PATH": '1',
    "MOZ_TOOLS": 'D:\\mozilla-build\\moztools',
    "NVAPSDK": 'd:\sdks\tegra042',
    "PATH": 'D:\\mozilla-build\\msys\\local\\bin;' + \
            'd:\\mozilla-build\\wget;' + \
            'd:\\mozilla-build\\7zip;' + \
            'd:\\mozilla-build\\blat261\\full;' + \
            'd:\\mozilla-build\\python25;' + \
            'd:\\mozilla-build\\svn-win32-1.4.2\\bin;' + \
            'd:\\mozilla-build\\upx203w;' + \
            'd:\\mozilla-build\\xemacs\\XEmacs-21.4.19\\i586-pc-win32;' + \
            'd:\\mozilla-build\\info-zip;' + \
            'd:\\mozilla-build\\nsis-2.22;' + \
            'd:\\mozilla-build\\nsis-2.33u;' + \
            '.;' + \
            'D:\\mozilla-build\\msys\\local\\bin;' + \
            'D:\\mozilla-build\\msys\\mingw\\bin;' + \
            'D:\\mozilla-build\\msys\\bin;' + \
            'd:\\msvs9\\Common7\\IDE;' + \
            'd:\\msvs9\\VC\\BIN;' + \
            'd:\\msvs9\\Common7\\Tools;' + \
            'c:\\WINDOWS\\Microsoft.NET\\Framework\\v3.5;' + \
            'c:\\WINDOWS\\Microsoft.NET\\Framework\\v2.0.50727;' + \
            'd:\\msvs9\\VC\\VCPackages;' + \
            'c:\\Program Files\\Microsoft SDKs\\Windows\\v6.0A\\bin;' + \
            'c:\\WINDOWS\\system32;' + \
            'c:\\WINDOWS;' + \
            'c:\\WINDOWS\\System32\\Wbem;' + \
            'd:\\mozilla-build\\python25;' + \
            'd:\\mozilla-build\\hg;' + \
            'c:\\Program Files\\Microsoft SQL Server\\90\\Tools\\binn\\;' + \
            'd:\\sdks\\tegra042\\tools;' + \
            'd:\\sdks\\tegra042\\platformlibs\\bin\\winxp\\x86\\release;' + \
            'd:\\sdks\\tegra042\\3rdparty\\bin\\winxp\\x86\\release;' + \
            'd:\\mozilla-build\\moztools\\bin',
    "VC8DIR": 'D:\\msvs8\\VC\\',
    "VC9DIR": 'd:\\msvs9\\VC\\',
    "VCINSTALLDIR": 'd:\\msvs9\\VC',
    "VS80COMNTOOLS": 'D:\\msvs8\\Common7\\Tools\\',
    "VS90COMNTOOLS": 'd:\\msvs9\\Common7\\Tools\\',
    "VSINSTALLDIR": 'd:\\msvs9',
}


### Talos environments
# platform SDK location.  we can build both from one generic template.
# modified from vc8 environment
MozillaEnvironments['win32-perf'] = {
    "MOZ_CRASHREPORTER_NO_REPORT": '1',
    "MOZ_NO_REMOTE": '1',
    "NO_EM_RESTART": '1',
    "XPCOM_DEBUG_BREAK": 'warn',
    "CYGWINBASE": 'C:\\cygwin',
    "PATH": 'C:\\Python24;' + \
            'C:\\Python24\\Scripts;' + \
            'C:\\cygwin\\bin;' + \
            'C:\\WINDOWS\\System32;' + \
            'C:\\program files\\gnuwin32\\bin;' + \
            'C:\\WINDOWS;'
}

MozillaEnvironments['linux-perf'] = {
    "MOZ_CRASHREPORTER_NO_REPORT": '1',
    "MOZ_NO_REMOTE": '1',
    "NO_EM_RESTART": '1',
    "XPCOM_DEBUG_BREAK": 'warn',
    "DISPLAY": ":0",
}

MozillaEnvironments['mac-perf'] = {
    "MOZ_NO_REMOTE": '1',
    "NO_EM_RESTART": '1',
    "XPCOM_DEBUG_BREAK": 'warn',
    "MOZ_CRASHREPORTER_NO_REPORT": '1',
    # for extracting dmg's
    "PAGER": '/bin/cat',
}
