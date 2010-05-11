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
  MaemoBuildFactory
from buildbotcustom.steps.misc import SendChangeStep
from buildbotcustom.steps.tryserver import MozillaTryProcessing, \
  MozillaDownloadMozconfig, MozillaPatchDownload, MozillaTryServerHgClone, \
  MozillaCustomPatch, MozillaCreateUploadDirectory, MozillaUploadTryBuild

class MaemoTryBuildFactory(MaemoBuildFactory):
    def __init__(self, scp_string=None, targetSubDir='maemo',
                 slavesrcdir='upload', baseBuildDir=None,
                 baseWorkDir=None, objdir=None, **kwargs):
        assert(baseWorkDir, baseBuildDir, objdir)
        self.scp_string = scp_string
        self.targetSubDir = targetSubDir
        self.slavesrcdir = slavesrcdir
        MaemoBuildFactory.__init__(self,
                                   objdirRelPath='%s/%s' % (baseBuildDir,
                                                            objdir),
                                   objdirAbsPath='%s/%s' % (baseWorkDir,
                                                            objdir),
                                   objdir=objdir, baseBuildDir=baseBuildDir,
                                   baseWorkDir=baseWorkDir, **kwargs)

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
            workdir=self.objdirAbsPath
        )


    # In MaemoBuildFactory's __init__, addPackageSteps gets called
    # with the argument 'packageTests' and we have to add here
    # even if it is not used
    def addPackageSteps(self, multiLocale=False, packageTests=False):
        MaemoBuildFactory.addPackageSteps(self, multiLocale=False, packageTests=True)
        self.addStep(ShellCommand,
            command=['mkdir', self.slavesrcdir],
            description=['create', 'upload', 'directory'],
            haltOnFailure=True,
            workdir=self.baseWorkDir)
        self.addStep(ShellCommand,
            command="cp %s %s/%s" % (self.packageGlob,
                                     self.baseWorkDir, self.slavesrcdir),
            description=['move', 'globs', 'to', 'upload', 'directory'],
            workdir=self.objdirAbsPath)
