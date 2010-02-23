import re
import os.path

import buildbot.steps.shell
reload(buildbot.steps.shell)

from buildbot.steps.shell import SetProperty, ShellCommand, WithProperties

import buildbotcustom.process.factory
import buildbotcustom.steps.misc
import buildbotcustom.steps.tryserver
reload(buildbotcustom.process.factory)
reload(buildbotcustom.steps.misc)
reload(buildbotcustom.steps.tryserver)

from buildbotcustom.process.factory import MercurialBuildFactory, \
  WinmoBuildFactory, MaemoBuildFactory
from buildbotcustom.steps.misc import SendChangeStep
from buildbotcustom.steps.tryserver import MozillaTryProcessing, \
  MozillaDownloadMozconfig, MozillaPatchDownload, MozillaTryServerHgClone, \
  MozillaCustomPatch, MozillaCreateUploadDirectory, MozillaUploadTryBuild


class TryBuildFactory(MercurialBuildFactory):
    def __init__(self, platform, configRepoPath=None, configSubDir=None,
                 mozconfig=None, uploadSymbols=False, env={},
                 talosMasters=None, unittestMasters=None, packageUrl=None,
                 packageDir=None, **kwargs):
        if talosMasters is None:
            self.talosMasters = []
        else:
            assert packageUrl
            self.talosMasters = talosMasters

        if unittestMasters is None:
            self.unittestMasters = []
        else:
            assert packageUrl
            self.unittestMasters = unittestMasters

        self.packageUrl = packageUrl
        # The directory the packages go into
        self.packageDir = packageDir
        # The filename minus the extension
        self.pkgBasename = '%(identifier)s-' + '%s' % platform

        MercurialBuildFactory.__init__(self, platform=platform,
                                       configRepoPath=configRepoPath,
                                       configSubDir=configSubDir,
                                       mozconfig=mozconfig,
                                       uploadSymbols=uploadSymbols,
                                       env=env, **kwargs)

    def addFilePropertiesSteps(self, filename=None, directory=None, 
                               fileType=None, maxDepth=1, haltOnFailure=False):
        # We don't need to do this for try builds.
        pass    

    def addPreBuildSteps(self):
        self.addStep(MozillaTryProcessing)
        if self.platform == 'win32':
            self.addStep(ShellCommand,
             command=['rmdir', '/s', '/q', 'build'],
             workdir='.',
             timeout=60*60
            )
        else:
            self.addStep(ShellCommand,
             command=['rm', '-rf', 'build'],
             workdir='.',
             timeout=60*60
            )

    def addSourceSteps(self):
        self.addStep(MozillaTryServerHgClone,
         workdir='build'
        )
        self.addStep(MozillaPatchDownload,
         patchDir='patches/',
         haltOnFailure=False,
         flunkOnFailure=True,
         isOptional=True
        )
        self.addStep(MozillaCustomPatch,
         haltOnFailure=True,
         flunkOnFailure=True,
         isOptional=True
        )

    def addConfigSteps(self):
        self.addStep(MozillaDownloadMozconfig,
         mastersrc='mozconfig-%s' % self.platform,
         patchDir='patches/',
         workdir='build'
        )

    def addUploadSteps(self):
        pkgArgs = [WithProperties('PKG_BASENAME=%s' % self.pkgBasename)]
        MercurialBuildFactory.addUploadSteps(self, pkgArgs=pkgArgs)

    def doUpload(self):
        uploadDir = '/'.join([self.stageBasePath, self.packageDir])
        uploadEnv = self.env.copy()
        uploadEnv.update({
            'UPLOAD_HOST': self.stageServer,
            'UPLOAD_USER': self.stageUsername,
            'UPLOAD_PATH': WithProperties(uploadDir)
        })

        if self.stageSshKey:
            uploadenv['UPLOAD_SSH_KEY'] = '~/.ssh/%s' % self.stageSshKey

        def get_url(rc, stdout, stderr):
            for m in re.findall('".*\.(?:tar\.bz2|dmg|zip)"', "\n".join([stdout, stderr])):
                if m.endswith("crashreporter-symbols.zip"):
                    continue
                if m.endswith("tests.tar.bz2"):
                    continue
                return {'uploadpath': os.path.basename(m).rstrip('"')}
            return {}

        self.addStep(SetProperty,
         command=['make', 'upload',
                  WithProperties('PKG_BASENAME=%s' % self.pkgBasename)],
         env=uploadEnv,
         workdir='build/%s' % self.objdir,
         extract_fn=get_url
        )

        fullUploadPath = '/'.join([self.packageUrl, self.packageDir,
                                  '%(uploadpath)s'])
        for master, warn in self.talosMasters:
            self.addStep(SendChangeStep,
             warnOnFailure=warn,
             master=master,
             branch=self.platform,
             files=[WithProperties(fullUploadPath)],
             user="sendchange"
            )
        unittestbranch = "sendchange-hg-%s" % self.platform
        for master, warn in self.unittestMasters:
            self.addStep(SendChangeStep,
             warnOnFailure=warn,
             master=master,
             branch=unittestBranch,
             files=[WithProperties(fullUploadPath)],
             user="sendchange-unittest"
            )


class MaemoTryBuildFactory(MaemoBuildFactory):
    def __init__(self, scp_string=None, targetSubDir='maemo',
                 slavesrcdir = 'upload', **kwargs):
        self.scp_string = scp_string
        self.targetSubDir = targetSubDir
        self.slavesrcdir = slavesrcdir
        MaemoBuildFactory.__init__(self, **kwargs)

    def getMozconfig(self):
        self.addStep(MozillaDownloadMozconfig, mastersrc="mozconfig-maemo",
                     workdir=self.baseWorkDir, patchDir="patches/")

    def addPreCleanSteps(self):
        self.addStep(MozillaTryProcessing)
        self.addStep(ShellCommand,
            name="remove source and obj dirs",
            command=["rm", "-rf", self.baseWorkDir],
            haltOnFailure=True,
            flunkOnFailure=True,
            workdir=self.baseWorkDir,
            timeout=60*60, # 1 hour
        )

    def addBaseRepoSteps(self):
        self.addStep(MozillaTryServerHgClone, workdir=self.baseWorkDir)
        self.addHgPullSteps(repository=self.mobileRepository,
                            workdir=self.baseWorkDir,
                            changesetLink=self.mobileChangesetLink,
                            targetDirectory='mobile')

    def addPreBuildSteps(self):
        self.addStep(MozillaPatchDownload,
            patchDir="patches/",
            haltOnFailure=False,
            flunkOnFailure=True,
            workdir=self.baseWorkDir,
            isOptional=True,
        )
        self.addStep(MozillaCustomPatch,
            workdir=self.baseWorkDir,
            haltOnFailure=True,
            flunkOnFailure=True,
            isOptional=True
        )
        self.addStep(ShellCommand,
            name="mozconfig contents",
            command=["cat", ".mozconfig"],
            workdir=self.baseWorkDir,
            description=["mozconfig", "contents"]
        )

    def addBuildSteps(self):
        self.addStep(ShellCommand,
            command=[self.scratchboxPath, '-p', '-d',
                     self.baseWorkDir,
                     'make -f client.mk build'],
            description=['compile'],
            env={'PKG_CONFIG_PATH' :
                 '/usr/lib/pkgconfig:/usr/local/lib/pkgconfig'},
            haltOnFailure=True
        )
        
    def addUploadSteps(self, platform=None):
        self.addStep(MozillaCreateUploadDirectory,
            scpString=self.scp_string,
            haltOnFailure=False,
            flunkOnFailure=False,
            targetSubDir=self.targetSubDir,
            workdir='.'
        )
        
        self.addStep(MozillaUploadTryBuild,
            baseFilename="",
            slavedir=self.baseWorkDir,
            slavesrcdir="%s/%s/*" % (self.baseWorkDir, self.slavesrcdir),
            scpString=self.scp_string,
            targetSubDir=self.targetSubDir,
            haltOnFailure=False,
            flunkOnFailure=False,
            workdir="%s/%s" % (self.baseWorkDir, self.objdir),
        )


    # In MaemoBuildFactory's __init__, addPackageSteps gets called
    # with the argument 'packageTests' and we have to add here
    # even if it is not used
    def addPackageSteps(self, multiLocale=False, packageTests=False):
        self.addStep(ShellCommand,
            command='mkdir upload',
            description=['create', 'upload', 'directory'],
            haltOnFailure=True,
            workdir=self.baseWorkDir)
        self.addStep(ShellCommand,
            command=[self.scratchboxPath, '-p', '-d',
                     '%s/%s/mobile' % (self.baseWorkDir, self.objdir),
                     'make package'],
            description=['make', 'package'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            command=[self.scratchboxPath, '-p', '-d',
                     '%s/%s/xulrunner' % (self.baseWorkDir, self.objdir),
                     'make package-tests PYTHON=python2.5'],
            description=['make', 'package-tests'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            command=[self.scratchboxPath, '-p', '-d',
                     '%s/%s/mobile' % (self.baseWorkDir,
                                                self.objdir),
                     'make deb'],
            description=['make', 'mobile', 'deb'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            command="cp %s %s/%s" % (self.packageGlob,
                                     self.baseWorkDir, self.slavesrcdir),
            description=['move', 'globs', 'to', 'upload', 'directory'],
            workdir="%s/%s" % (self.baseWorkDir, self.objdir))


class WinmoTryBuildFactory(WinmoBuildFactory):
    def __init__(self, scp_string=None, targetSubDir='winmo',
                 slavesrcdir='upload', **kwargs):
        self.scp_string = scp_string
        self.targetSubDir=targetSubDir
        self.slavesrcdir=slavesrcdir
        WinmoBuildFactory.__init__(self, **kwargs)

    def getMozconfig(self):
        self.addStep(MozillaDownloadMozconfig, mastersrc="mozconfig-winmo",
                                               patchDir="patches/",
                                               workdir=self.baseWorkDir)

    def addPreCleanSteps(self):
        self.addStep(MozillaTryProcessing)
        self.addStep(ShellCommand,
            name="remove source and obj dirs",
            command=["rmdir", "/s", "/q", "%s" % self.baseWorkDir],
            haltOnFailure=True,
            flunkOnFailure=True,
            workdir=".",
            timeout=60*60, # 1 hour
        )
        
    def addBaseRepoSteps(self):
        self.addStep(MozillaTryServerHgClone, workdir=self.baseWorkDir)
        self.addHgPullSteps(repository=self.mobileRepository,
                            workdir=self.baseWorkDir,
                            changesetLink=self.mobileChangesetLink,
                            targetDirectory='mobile')

    def addPreBuildSteps(self):
        self.addStep(MozillaPatchDownload,
            patchDir="patches/",
            haltOnFailure=False,
            flunkOnFailure=True,
            workdir=self.baseWorkDir,
            isOptional=True,
        )
        self.addStep(MozillaCustomPatch,
            workdir=self.baseWorkDir,
            haltOnFailure=True,
            flunkOnFailure=True,
            isOptional=True
        )
        self.addStep(ShellCommand,
            name="mozconfig contents",
            command=["cat", ".mozconfig"],
            workdir=self.baseWorkDir,
            description=["mozconfig", "contents"]
        )

    def addBuildSteps(self):
        self.addStep(ShellCommand,
            command=['make', '-f', 'client.mk', 'build'],
            description=['compile'],
            workdir=self.baseWorkDir,
            env=self.env,
            haltOnFailure=True
        )
        
    def addUploadSteps(self, platform=None):
        self.addStep(MozillaCreateUploadDirectory,
            scpString=self.scp_string,
            haltOnFailure=False,
            flunkOnFailure=False,
            targetSubDir=self.targetSubDir,
            workdir='.'
        )
        
        self.addStep(MozillaUploadTryBuild,
            slavedir=self.baseWorkDir,
            baseFilename="",
            slavesrcdir=r"%s/*" % self.slavesrcdir,
            scpString=self.scp_string,
            targetSubDir=self.targetSubDir,
            haltOnFailure=False,
            flunkOnFailure=False,
        )
    def addPackageSteps(self):
        self.addStep(ShellCommand,
            command='mkdir upload',
            description=['create', 'upload', 'directory'],
            haltOnFailure=True,
            workdir=self.baseWorkDir)
        self.addStep(ShellCommand,
            command=['make', 'package'],
            workdir='%s\%s\mobile' % (self.baseWorkDir, self.objdir),
            description=['make', 'package'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            command=['make', 'installer'],
            workdir='%s\%s\mobile' % (self.baseWorkDir, self.objdir),
            description=['make', 'installer'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            name='make_pkg_tests',
            command=['make', 'package-tests'],
            workdir='%s/%s/xulrunner' % (self.baseWorkDir, self.objdir),
            description=['make', 'package-tests'],
            haltOnFailure=True
        )
        self.addStep(ShellCommand,
            command="cp %s ../%s" % (self.packageGlob, self.slavesrcdir),
            description=['copy', 'globs', 'to', 'upload', 'directory'],
            workdir="%s\%s" % (self.baseWorkDir, self.objdir))
