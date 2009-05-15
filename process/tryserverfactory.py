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

from buildbotcustom.process.factory import MercurialBuildFactory
from buildbotcustom.steps.misc import SendChangeStep
from buildbotcustom.steps.tryserver import MozillaTryProcessing, \
  MozillaDownloadMozconfig, MozillaPatchDownload, MozillaTryServerHgClone, \
  MozillaCustomPatch


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

    def addPreBuildSteps(self):
        self.addStep(MozillaTryProcessing)
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
