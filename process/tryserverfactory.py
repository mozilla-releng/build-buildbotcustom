import buildbot.steps.shell
reload(buildbot.steps.shell)

from buildbot.steps.shell import ShellCommand, WithProperties

import buildbotcustom.process.factory
import buildbotcustom.steps.tryserver
reload(buildbotcustom.process.factory)
reload(buildbotcustom.steps.tryserver)

from buildbotcustom.process.factory import MercurialBuildFactory
from buildbotcustom.steps.tryserver import MozillaTryProcessing, \
  MozillaDownloadMozconfig, MozillaPatchDownload, MozillaTryServerHgClone, \
  MozillaCustomPatch


class TryBuildFactory(MercurialBuildFactory):
    def __init__(self, platform, configRepoPath=None, configSubDir=None,
                 mozconfig=None, productName='firefox-try', uploadSymbols=False,
                 env={}, talosMasters=None, unittestMasters=None, **kwargs):
        if talosMasters is None:
            self.talosMasters = []
        else:
            self.talosMasters = talosMasters

        if unittestMasters is None:
            self.unittestMasters = []
        else:
            self.unittestMasters = unittestMasters

        self.pkgBasename = '%(identifier)s-' + '%s-%s' % (productName, platform)

        MercurialBuildFactory.__init__(self, platform=platform,
                                       configRepoPath=configRepoPath,
                                       configSubDir=configSubDir,
                                       mozconfig=mozconfig,
                                       productName=productName,
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
        pkgBasename = '%(identifier)s-' + '%s-%s' % (self.productName,
                                                     self.platform)
        uploadEnv = self.env.copy()
        uploadEnv.update({'UPLOAD_HOST': self.stageServer,
                          'UPLOAD_USER': self.stageUsername,
                          'UPLOAD_PATH': self.stageBasePath})

        if self.stageSshKey:
            uploadenv['UPLOAD_SSH_KEY'] = '~/.ssh/%s' % self.stageSshKey

        # TODO get_url
        self.addStep(ShellCommand,
         command=['make', 'upload',
                  WithProperties('PKG_BASENAME=%s' % self.pkgBasename)],
         env=uploadEnv,
         workdir='build/%s' % self.objdir
        )
        # TODO: sendchange
