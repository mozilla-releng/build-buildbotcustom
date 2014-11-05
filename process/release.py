# The absolute_import directive looks firstly at what packages are available
# on sys.path to avoid name collisions when we import release.* from elsewhere
from __future__ import absolute_import

import os
import hashlib
from buildbot.process.buildstep import regex_log_evaluator
from buildbot.scheduler import Scheduler, Dependent, Triggerable
from buildbot.status.mail import MailNotifier
from buildbot.steps.trigger import Trigger
from buildbot.status.builder import Results
from buildbot.process.factory import BuildFactory

import release.platforms
import release.paths
import buildbotcustom.common
import build.paths
import release.info
reload(release.platforms)
reload(release.paths)
reload(build.paths)
reload(release.info)

from buildbotcustom.status.mail import ChangeNotifier
from buildbotcustom.misc import get_l10n_repositories, \
    generateTestBuilderNames, generateTestBuilder, \
    changeContainsProduct, nomergeBuilders, changeContainsProperties, \
    changeBaseTagContainsScriptRepoRevision, _nextSlave_skip_spot
from buildbotcustom.common import normalizeName
from buildbotcustom.process.factory import StagingRepositorySetupFactory, \
    ScriptFactory, SingleSourceFactory, ReleaseBuildFactory, \
    ReleaseUpdatesFactory, ReleaseFinalVerification, \
    PartnerRepackFactory, XulrunnerReleaseBuildFactory, \
    makeDummyBuilder, SigningScriptFactory
from release.platforms import buildbot2ftp
from release.paths import makeCandidatesDir
from buildbotcustom.scheduler import TriggerBouncerCheck, \
    makePropertiesScheduler, AggregatingScheduler
from buildbotcustom.misc_scheduler import buildIDSchedFunc, buildUIDSchedFunc
from buildbotcustom.status.errors import update_verify_error, \
    permission_check_error
from build.paths import getRealpath
from release.info import getRuntimeTag, getReleaseTag
import BuildSlaves

DEFAULT_PARALLELIZATION = 10


def generateReleaseBranchObjects(releaseConfig, branchConfig,
                                 releaseConfigFile, sourceRepoKey="mozilla",
                                 secrets=None):
    # This variable is one thing that forces us into reconfiging prior to a
    # release. It should be removed as soon as nothing depends on it.
    sourceRepoInfo = releaseConfig['sourceRepositories'][sourceRepoKey]
    releaseTag = getReleaseTag(releaseConfig['baseTag'])
    # This tag is created post-signing, when we do some additional
    # config file bumps
    runtimeTag = getRuntimeTag(releaseTag)
    l10nChunks = releaseConfig.get('l10nChunks', DEFAULT_PARALLELIZATION)
    releaseChannel = releaseConfig.get('releaseChannel', 'release')
    updateVerifyChunks = releaseConfig.get(
        'updateVerifyChunks', DEFAULT_PARALLELIZATION)
    tools_repo_path = releaseConfig.get('build_tools_repo_path',
                                        branchConfig['build_tools_repo_path'])
    tools_repo = '%s%s' % (branchConfig['hgurl'], tools_repo_path)
    config_repo = '%s%s' % (branchConfig['hgurl'],
                            branchConfig['config_repo_path'])
    mozharness_repo_path = releaseConfig.get('mozharness_repo_path',
                                             branchConfig['mozharness_repo_path'])
    mozharness_repo = '%s%s' % (branchConfig['hgurl'], mozharness_repo_path)
    clobberer_url = releaseConfig.get('base_clobber_url',
                                      branchConfig['base_clobber_url'])
    balrog_api_root = releaseConfig.get('balrog_api_root',
                                        branchConfig.get('balrog_api_root', None))
    balrog_username = releaseConfig.get('balrog_username',
                                        branchConfig.get('balrog_username', None))

    branchConfigFile = getRealpath('localconfig.py')
    unix_slaves = []
    mock_slaves = []
    all_slaves = []
    for p in branchConfig['platforms']:
        if p == 'b2g':
            continue
        platform_slaves = branchConfig['platforms'][p].get('slaves', [])
        all_slaves.extend(platform_slaves)
        if 'win' not in p:
            unix_slaves.extend(platform_slaves)
            if branchConfig['platforms'][p].get('use_mock'):
                mock_slaves.extend(platform_slaves)
    unix_slaves = [x for x in set(unix_slaves)]
    mock_slaves = [x for x in set(mock_slaves)]
    all_slaves = [x for x in set(all_slaves)]

    if secrets is None:
        secrets = {}

    def getSigningServers(platform):
        signingServers = secrets.get('release-signing')
        assert signingServers, 'Please provide a valid list of signing servers'
        return signingServers

    def builderPrefix(s, platform=None):
        if platform:
            return "release-%s-%s_%s" % (sourceRepoInfo['name'], platform, s)
        else:
            return "release-%s-%s" % (sourceRepoInfo['name'], s)

    def releasePrefix():
        """Construct a standard format product release name from the
           product name, version and build number stored in release_config.py
        """
        return "%s %s build%s" % (
            releaseConfig['productName'].title(),
            releaseConfig['version'],
            releaseConfig['buildNumber'], )

    def genericFtpUrl():
        """ Generate an FTP URL pointing to the uploaded release builds for
        sticking into release notification messages """
        return makeCandidatesDir(
            releaseConfig['productName'],
            releaseConfig['version'],
            releaseConfig['buildNumber'],
            protocol='ftp',
            server=releaseConfig['ftpServer'])

    def genericHttpsUrl():
        """ Generate an HTTPS URL pointing to the uploaded release builds for
        sticking into release notification messages """
        return makeCandidatesDir(
            releaseConfig['productName'],
            releaseConfig['version'],
            releaseConfig['buildNumber'],
            protocol='https',
            server=releaseConfig['ftpServer'])

    def createReleaseMessage(mode, name, build, results, master_status):
        """Construct a standard email to send to release@/release-drivers@
           whenever a major step of the release finishes
        """
        msgdict = {}
        releaseName = releasePrefix()
        job_status = "failed" if results else "success"
        job_status_repr = Results[results]
        allplatforms = list(releaseConfig['enUSPlatforms'])
        xrplatforms = list(releaseConfig.get('xulrunnerPlatforms', []))
        stage = name.replace(builderPrefix(""), "")
        releaseChannel = releaseConfig.get("releaseChannel")
        cdnTestChannel = releaseConfig.get("cdnTestChannel")
        localTestChannel = releaseConfig.get("localTestChannel")

        # Detect platform from builder name by tokenizing by '_', and matching
        # the first token after the prefix
        if stage.startswith("xulrunner"):
            platform = ["xulrunner_%s" % p for p in xrplatforms
                        if stage.replace("xulrunner_", "").split('_')[0] == p]
        else:
            platform = [p for p in allplatforms if stage.split('_')[0] == p]
        platform = platform[0] if len(platform) >= 1 else ''
        bare_platform = platform.replace('xulrunner_', '')
        message_tag = getMessageTag()
        buildbot_url = ''
        if master_status.getURLForThing(build):
            buildbot_url = "Full details are available at:\n %s\n" % master_status.getURLForThing(build)
        # Use a generic ftp URL non-specific to any locale
        ftpURL = genericFtpUrl()
        if 'xulrunner' in platform:
            ftpURL = ftpURL.replace(releaseConfig['productName'], 'xulrunner')
        isPlatformUnsigned = False
        if platform:
            platformDir = buildbot2ftp(bare_platform)
            if 'xulrunner' in platform:
                platformDir = ''
            ftpURL = '/'.join([
                ftpURL.strip('/'),
                platformDir])

        stage = stage.replace("%s_" % platform, "") if platform else stage
        # try to load a unique message template for the platform(if defined, step and results
        # if none exists, fall back to the default template
        possible_templates = ["%s/%s_%s_%s" % (releaseConfig['releaseTemplates'], platform, stage, job_status),
                              "%s/%s_%s" % (
                                  releaseConfig[
                                      'releaseTemplates'], stage, job_status),
                              "%s/%s_default_%s" % (
                                  releaseConfig[
                                      'releaseTemplates'], platform, job_status),
                              "%s/default_%s" % (releaseConfig['releaseTemplates'], job_status)]
        if isPlatformUnsigned:
            unsigned_templates = []
            for t in possible_templates:
                path, template = os.path.split(t)
                unsigned_templates.append(
                    os.path.join(path, 'unsigned_%s' % template))
            possible_templates = possible_templates + unsigned_templates
        template = None
        for t in possible_templates:
            if os.access(t, os.R_OK):
                template = open(t, "r", True)
                break

        if template:
            subject = message_tag + template.readline().strip() % locals()
            body = ''.join(template.readlines())
            template.close()
        else:
            raise IOError("Cannot find a template file to use")
        msgdict['subject'] = subject % locals()
        msgdict['body'] = body % locals() + "\n"
        msgdict['type'] = 'plain'
        return msgdict

    def createReleaseChangeMessage(change):
        """Construct a standard email to send to release@/release-drivers@
           whenever a change is pushed to a release-related branch being
           listened on"""
        msgdict = {}
        releaseName = releasePrefix()
        message_tag = getMessageTag()
        step = None
        ftpURL = genericFtpUrl()
        if change.branch.endswith('signing'):
            step = "signing"
        else:
            step = "tag"
        # try to load a unique message template for the change
        # if none exists, fall back to the default template
        possible_templates = (
            "%s/%s_change" % (releaseConfig['releaseTemplates'], step),
            "%s/default_change" % releaseConfig['releaseTemplates'])
        template = None
        for t in possible_templates:
            if os.access(t, os.R_OK):
                template = open(t, "r", True)
                break

        if template:
            subject = message_tag + template.readline().strip() % locals()
            body = ''.join(template.readlines()) + "\n"
            template.close()
        else:
            raise IOError("Cannot find a template file to use")
        msgdict['subject'] = subject % locals()
        msgdict['body'] = body % locals()
        msgdict['type'] = 'plain'
        return msgdict

    def createReleaseAVVendorsMessage(mode, name, build, results, master_status):
        """Construct the release notification email to send to the AV Vendors.
        """
        template_name = "%s/updates_avvendors" % releaseConfig[
            'releaseTemplates']
        if not os.access(template_name, os.R_OK):
            raise IOError("Cannot find a template file to use")

        template = open(template_name, "r", True)
        subject = getMessageTag() + '%(productName)s %(version)s release'
        body = ''.join(template.readlines())
        template.close()

        productName = releaseConfig['productName'].title()
        version = releaseConfig['version']
        buildsURL = genericHttpsUrl()

        msgdict = {}
        msgdict['subject'] = subject % locals()
        msgdict['body'] = body % locals() + "\n"
        msgdict['type'] = 'plain'
        return msgdict

    def getMessageTag():
        return releaseConfig.get('messagePrefix', '[release] ')

    def parallelizeBuilders(base_name, platform, chunks):
        builders = {}
        for n in range(1, chunks + 1):
            builders[n] = builderPrefix("%s_%s/%s" % (base_name, n,
                                                      str(chunks)),
                                        platform)
        return builders

    def l10nBuilders(platform):
        return parallelizeBuilders("repack", platform, l10nChunks)

    def updateVerifyBuilders(platform):
        return parallelizeBuilders("update_verify", platform,
                                   updateVerifyChunks)

    def hasPlatformSubstring(platforms, substring):
        if isinstance(platforms, basestring):
            platforms = (platforms,)
        return bool([p for p in platforms if substring in p])

    def use_mock(platform):
        pf = branchConfig['platforms'][platform]
        if releaseConfig.get('use_mock', pf.get('use_mock')):
            if platform in releaseConfig['mock_platforms']:
                return True
        return False

    def getMessageId():
        md5 = hashlib.md5(getMessageTag())
        for key in ("version", "buildNumber", "productName"):
            md5.update(str(releaseConfig.get(key)))
        return "<%s@mozilla.com>" % md5.hexdigest()

    builders = []
    test_builders = []
    schedulers = []
    change_source = []
    important_builders = []
    status = []
    updates_upstream_builders = []
    post_signing_builders = []
    post_update_builders = []
    deliverables_builders = []
    xr_deliverables_builders = []
    post_deliverables_builders = []
    post_antivirus_builders = []
    email_message_id = getMessageId()

    # Builders #

    builder_env = {
        'BUILDBOT_CONFIGS': '%s%s' % (branchConfig['hgurl'],
                                      branchConfig['config_repo_path']),
        'BUILDBOTCUSTOM': '%s%s' % (branchConfig['hgurl'],
                                    branchConfig['buildbotcustom_repo_path']),
        'CLOBBERER_URL': clobberer_url,
    }

    if use_mock('linux'):
        unix_slaves = mock_slaves
    if releaseConfig.get('enable_repo_setup'):
        if not releaseConfig.get('skip_repo_setup'):
            clone_repositories = dict()
            # The repo_setup builder only needs to the repoPath, so we only
            # give it that
            for sr in releaseConfig['sourceRepositories'].values():
                clone_repositories.update({sr['clonePath']: {}})
            # get_l10n_repositories spits out more than just the repoPath
            # It's easier to just pass it along rather than strip it out
            if len(releaseConfig['l10nPlatforms']) > 0:
                l10n_clone_repos = get_l10n_repositories(
                    releaseConfig['l10nRevisionFile'],
                    releaseConfig['l10nRepoClonePath'],
                    sourceRepoInfo['relbranch'])
                clone_repositories.update(l10n_clone_repos)

            pf = branchConfig['platforms']['linux']
            hgSshKey = releaseConfig['hgSshKey']
            repository_setup_factory = StagingRepositorySetupFactory(
                hgHost=branchConfig['hghost'],
                buildToolsRepoPath=tools_repo_path,
                username=releaseConfig['hgUsername'],
                sshKey=hgSshKey,
                repositories=clone_repositories,
                clobberURL=clobberer_url,
                clobberBranch='release-%s' % sourceRepoInfo['name'],
                userRepoRoot=releaseConfig['userRepoRoot'],
                use_mock=use_mock('linux'),
                mock_target=pf.get('mock_target'),
                mock_packages=pf.get('mock_packages'),
                mock_copyin_files=pf.get('mock_copyin_files'),
                env=pf['env'],
            )

            builders.append({
                'name': builderPrefix(
                    '%s_repo_setup' % releaseConfig['productName']),
                'slavenames': unix_slaves,
                'category': builderPrefix(''),
                'builddir': builderPrefix(
                    '%s_repo_setup' % releaseConfig['productName']),
                'slavebuilddir': normalizeName(builderPrefix(
                    '%s_repo_setup' % releaseConfig['productName']), releaseConfig['productName']),
                'factory': repository_setup_factory,
                'env': builder_env,
                'properties': {
                    'slavebuilddir': normalizeName(builderPrefix(
                        '%s_repo_setup' % releaseConfig['productName']), releaseConfig['productName']),
                    'release_config': releaseConfigFile,
                    'platform': None,
                    'branch': 'release-%s' % sourceRepoInfo['name'],
                },
            })
        else:
            builders.append(makeDummyBuilder(
                name=builderPrefix(
                    '%s_repo_setup' % releaseConfig['productName']),
                slaves=all_slaves,
                category=builderPrefix(''),
                properties={
                    'platform': None,
                    'branch': 'release-%s' % sourceRepoInfo['name'],
                },
            ))

    if not releaseConfig.get('skip_tag'):
        pf = branchConfig['platforms']['linux']
        tag_env = builder_env.copy()
        if pf['env'].get('PATH'):
            tag_env['PATH'] = pf['env']['PATH']
        if pf['env'].get('HG_SHARE_BASE_DIR', None):
            tag_env['HG_SHARE_BASE_DIR'] = pf['env']['HG_SHARE_BASE_DIR']

        tag_factory = ScriptFactory(
            scriptRepo=tools_repo,
            scriptName='scripts/release/tagging.sh',
            use_mock=use_mock('linux'),
            mock_target=pf.get('mock_target'),
            mock_packages=pf.get('mock_packages'),
            mock_copyin_files=pf.get('mock_copyin_files'),
            env=tag_env,
        )

        builders.append({
            'name': builderPrefix('%s_tag' % releaseConfig['productName']),
            'slavenames': pf['slaves'],
            'category': builderPrefix(''),
            'builddir': builderPrefix('%s_tag' % releaseConfig['productName']),
            'slavebuilddir': normalizeName(
                builderPrefix('%s_tag' % releaseConfig['productName'])),
            'factory': tag_factory,
            'nextSlave': _nextSlave_skip_spot,
            'env': tag_env,
            'properties': {
                'builddir': builderPrefix(
                    '%s_tag' % releaseConfig['productName']),
                'slavebuilddir': normalizeName(
                    builderPrefix('%s_tag' % releaseConfig['productName'])),
                'release_config': releaseConfigFile,
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
                'event_group': 'tag',
            }
        })
    else:
        builders.append(makeDummyBuilder(
            name=builderPrefix('%s_tag' % releaseConfig['productName']),
            slaves=all_slaves,
            category=builderPrefix(''),
            properties={
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
            },
        ))

    if not releaseConfig.get('skip_source'):
        pf = branchConfig['platforms']['linux']
        mozconfig = 'linux/%s/release' % sourceRepoInfo['name']
        platform_env = pf['env'].copy()
        platform_env['COMM_REV'] = releaseTag
        platform_env['MOZILLA_REV'] = releaseTag
        # do not use use_mock(platform) check because we are building source
        # packages on the linux platform, else the |platform in mock_platforms|
        # check will fail for android.
        source_use_mock = releaseConfig.get('use_mock')

        source_factory = SingleSourceFactory(
            env=platform_env,
            objdir=pf['platform_objdir'],
            hgHost=branchConfig['hghost'],
            buildToolsRepoPath=tools_repo_path,
            repoPath=sourceRepoInfo['path'],
            productName=releaseConfig['productName'],
            version=releaseConfig['version'],
            appVersion=releaseConfig['appVersion'],
            baseTag=releaseConfig['baseTag'],
            stagingServer=branchConfig['stage_server'],
            stageUsername=branchConfig['stage_username'],
            stageSshKey=branchConfig['stage_ssh_key'],
            buildNumber=releaseConfig['buildNumber'],
            autoconfDirs=['.', 'js/src'],
            clobberURL=clobberer_url,
            clobberBranch='release-%s' % sourceRepoInfo['name'],
            mozconfig=mozconfig,
            configRepoPath=branchConfig['config_repo_path'],
            configSubDir=branchConfig['config_subdir'],
            signingServers=getSigningServers('linux'),
            mozconfigBranch=releaseTag,
            use_mock=source_use_mock,
            mock_target=pf.get('mock_target'),
            mock_packages=pf.get('mock_packages'),
            mock_copyin_files=pf.get('mock_copyin_files'),
        )

        builders.append({
                        'name': builderPrefix('%s_source' % releaseConfig['productName']),
                        'slavenames': branchConfig['platforms']['linux']['slaves'] +
                        branchConfig['platforms']['linux64']['slaves'],
                        'category': builderPrefix(''),
                        'builddir': builderPrefix(
                            '%s_source' % releaseConfig['productName']),
                        'slavebuilddir': normalizeName(
                            builderPrefix(
                                '%s_source' % releaseConfig['productName']), releaseConfig['productName']),
                        'factory': source_factory,
                        'env': builder_env,
                        'properties': {
                            'slavebuilddir': normalizeName(
                                builderPrefix(
                                    '%s_source' % releaseConfig['productName']), releaseConfig['productName']),
                            'platform': None,
                            'branch': 'release-%s' % sourceRepoInfo['name'],
                        }
                        })
        deliverables_builders.append(
            builderPrefix('%s_source' % releaseConfig['productName']))

        if releaseConfig.get('xulrunnerPlatforms'):
            mozconfig = 'linux/%s/xulrunner' % sourceRepoInfo['name']
            xulrunner_source_factory = SingleSourceFactory(
                env=pf['env'],
                objdir=pf['platform_objdir'],
                hgHost=branchConfig['hghost'],
                buildToolsRepoPath=tools_repo_path,
                repoPath=sourceRepoInfo['path'],
                productName='xulrunner',
                version=releaseConfig['version'],
                appVersion=releaseConfig['appVersion'],
                baseTag=releaseConfig['baseTag'],
                stagingServer=branchConfig['stage_server'],
                stageUsername=branchConfig['stage_username_xulrunner'],
                stageSshKey=branchConfig['stage_ssh_xulrunner_key'],
                buildNumber=releaseConfig['buildNumber'],
                autoconfDirs=['.', 'js/src'],
                clobberURL=clobberer_url,
                clobberBranch='release-%s' % sourceRepoInfo['name'],
                mozconfig=mozconfig,
                configRepoPath=branchConfig['config_repo_path'],
                configSubDir=branchConfig['config_subdir'],
                signingServers=getSigningServers('linux'),
                mozconfigBranch=releaseTag,
                use_mock=use_mock('linux'),
                mock_target=pf.get('mock_target'),
                mock_packages=pf.get('mock_packages'),
                mock_copyin_files=pf.get('mock_copyin_files'),
            )

            builders.append({
                            'name': builderPrefix('xulrunner_source'),
                            'slavenames': branchConfig['platforms']['linux']['slaves'] +
                            branchConfig['platforms']['linux64']['slaves'],
                            'category': builderPrefix(''),
                            'builddir': builderPrefix('xulrunner_source'),
                            'slavebuilddir': normalizeName(builderPrefix('xulrunner_source'), releaseConfig['productName']),
                            'factory': xulrunner_source_factory,
                            'env': builder_env,
                            'properties': {
                                'slavebuilddir': normalizeName(builderPrefix('xulrunner_source'), releaseConfig['productName']),
                                'platform': None,
                                'branch': 'release-%s' % sourceRepoInfo['name'],
                                'product': 'xulrunner',
                            }
                            })
            xr_deliverables_builders.append(builderPrefix('xulrunner_source'))
    else:
        builders.append(makeDummyBuilder(
            name=builderPrefix('%s_source' % releaseConfig['productName']),
            slaves=all_slaves,
            category=builderPrefix(''),
            properties={
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
            },
        ))
        if releaseConfig.get('xulrunnerPlatforms'):
            builders.append(makeDummyBuilder(
                name=builderPrefix('xulrunner_source'),
                slaves=all_slaves,
                category=builderPrefix(''),
                properties={
                    'platform': None,
                    'branch': 'release-%s' % sourceRepoInfo['name'],
                },
            ))
            xr_deliverables_builders.append(builderPrefix('xulrunner_source'))

    mozillaDir = None
    mozillaSrcDir = None
    if 'mozilla_dir' in releaseConfig:
        mozillaDir = releaseConfig['mozilla_dir']
    if 'mozilla_srcdir' in releaseConfig:
        mozillaSrcDir = releaseConfig['mozilla_srcdir']

    partialUpdates = releaseConfig.get('partialUpdates', {}).copy()
    partialUpdates.update(releaseConfig.get('extraPartials', {}))

    for platform in releaseConfig['enUSPlatforms']:
        # shorthand
        pf = branchConfig['platforms'][platform]
        mozconfig = '%s/%s/release' % (platform, sourceRepoInfo['name'])
        if platform in releaseConfig['talosTestPlatforms']:
            talosMasters = pf['talos_masters']
        else:
            talosMasters = None

        if releaseConfig['enableUnittests']:
            packageTests = True
            unittestMasters = branchConfig['unittest_masters']
            unittestBranch = builderPrefix('%s-opt-unittest' % platform)
        else:
            packageTests = False
            unittestMasters = None
            unittestBranch = None

        if not releaseConfig.get('skip_build'):
            platform_env = pf['env'].copy()
            platform_env['MOZ_UPDATE_CHANNEL'] = releaseChannel
            platform_env['COMM_REV'] = releaseTag
            platform_env['MOZILLA_REV'] = releaseTag
            if platform in releaseConfig['l10nPlatforms']:
                triggeredSchedulers = [builderPrefix('%s_repack' % platform)]
            else:
                triggeredSchedulers = None
            multiLocaleConfig = releaseConfig.get(
                'multilocale_config', {}).get('platforms', {}).get(platform)
            mozharnessMultiOptions = releaseConfig.get(
                'multilocale_config', {}).get('multilocaleOptions')
            balrog_credentials_file = releaseConfig.get('balrog_credentials_file',
                branchConfig.get('balrog_credentials_file', None))
            # Turn pymake on by default for Windows, and off by default for
            # other platforms.
            if 'win' in platform:
                enable_pymake = pf.get('enable_pymake', True)
            else:
                enable_pymake = pf.get('enable_pymake', False)
            build_factory = ReleaseBuildFactory(
                env=platform_env,
                objdir=pf['platform_objdir'],
                platform=platform,
                hgHost=branchConfig['hghost'],
                repoPath=sourceRepoInfo['path'],
                buildToolsRepoPath=tools_repo_path,
                configRepoPath=branchConfig['config_repo_path'],
                configSubDir=branchConfig['config_subdir'],
                profiledBuild=pf['profiled_build'],
                mozconfig=mozconfig,
                srcMozconfig=releaseConfig.get('mozconfigs', {}).get(platform),
                buildRevision=releaseTag,
                stageServer=branchConfig['stage_server'],
                stageUsername=branchConfig['stage_username'],
                stageGroup=branchConfig['stage_group'],
                stageSshKey=branchConfig['stage_ssh_key'],
                stageBasePath=branchConfig['stage_base_path'],
                uploadPackages=True,
                uploadSymbols=True,
                createSnippet=False,
                doCleanup=True,
                # this will clean-up the mac build dirs, but not delete
                # the entire thing
                buildSpace=pf.get(
                    'build_space', branchConfig['default_build_space']),
                productName=releaseConfig['productName'],
                version=releaseConfig['version'],
                appVersion=releaseConfig['appVersion'],
                buildNumber=releaseConfig['buildNumber'],
                partialUpdates=partialUpdates,
                talosMasters=talosMasters,
                packageTests=packageTests,
                unittestMasters=unittestMasters,
                unittestBranch=unittestBranch,
                clobberURL=clobberer_url,
                clobberBranch='release-%s' % sourceRepoInfo['name'],
                triggerBuilds=True,
                triggeredSchedulers=triggeredSchedulers,
                stagePlatform=buildbot2ftp(platform),
                multiLocale=bool(releaseConfig.get('enableMultiLocale', False) and
                                 pf.get('multi_locale', False)),
                multiLocaleMerge=releaseConfig.get('mergeLocales', False),
                compareLocalesRepoPath=branchConfig[
                    'compare_locales_repo_path'],
                mozharnessRepoPath=mozharness_repo_path,
                mozharnessTag=releaseTag,
                multiLocaleScript=pf.get('multi_locale_script'),
                multiLocaleConfig=multiLocaleConfig,
                mozharnessMultiOptions=mozharnessMultiOptions,
                usePrettyNames=releaseConfig.get('usePrettyNames', True),
                enableUpdatePackaging=releaseConfig.get(
                    'enableUpdatePackaging', True),
                mozconfigBranch=releaseTag,
                signingServers=getSigningServers(platform),
                createPartial=releaseConfig.get(
                    'enablePartialMarsAtBuildTime', True),
                mozillaDir=mozillaDir,
                mozillaSrcDir=mozillaSrcDir,
                enableInstaller=pf.get('enable_installer', False),
                tooltool_manifest_src=pf.get('tooltool_manifest_src', None),
                tooltool_url_list=branchConfig.get('tooltool_url_list', []),
                tooltool_script=pf.get('tooltool_script'),
                use_mock=use_mock(platform),
                mock_target=pf.get('mock_target'),
                mock_packages=pf.get('mock_packages'),
                mock_copyin_files=pf.get('mock_copyin_files'),
                enable_pymake=enable_pymake,
                balrog_api_root=balrog_api_root,
                balrog_username=balrog_username,
                balrog_credentials_file=balrog_credentials_file,
            )

            builders.append({
                'name': builderPrefix('%s_build' % platform),
                'slavenames': pf['slaves'],
                'category': builderPrefix(''),
                'builddir': builderPrefix('%s_build' % platform),
                'slavebuilddir': normalizeName(builderPrefix('%s_build' % platform), releaseConfig['productName']),
                'factory': build_factory,
                'nextSlave': _nextSlave_skip_spot,
                'env': builder_env,
                'properties': {
                    'slavebuilddir': normalizeName(builderPrefix('%s_build' % platform), releaseConfig['productName']),
                    'platform': platform,
                    'branch': 'release-%s' % sourceRepoInfo['name'],
                    'event_group': 'build',
                },
            })
        else:
            builders.append(makeDummyBuilder(
                name=builderPrefix('%s_build' % platform),
                slaves=all_slaves,
                category=builderPrefix(''),
                properties={
                    'platform': platform,
                    'branch': 'release-%s' % sourceRepoInfo['name'],
                    'event_group': 'build',
                },
            ))
        updates_upstream_builders.append(builderPrefix('%s_build' % platform))
        deliverables_builders.append(builderPrefix('%s_build' % platform))

        if platform in releaseConfig['l10nPlatforms']:
            env = builder_env.copy()
            env.update(pf['env'])
            env['MOZ_UPDATE_CHANNEL'] = releaseChannel
            env['COMM_REV'] = releaseTag
            env['MOZILLA_REV'] = releaseTag

            if not releaseConfig.get('disableStandaloneRepacks'):
                extra_args = [platform, branchConfigFile]
                extra_args.extend([
                    '--stage-ssh-key', branchConfig['stage_ssh_key'],
                    '--stage-server', branchConfig['stage_server'],
                    '--stage-username', branchConfig['stage_username'],
                    '--hghost', branchConfig['hghost'],
                    '--compare-locales-repo-path',
                    branchConfig['compare_locales_repo_path']
                ])
                if releaseConfig.get('enablePartialMarsAtBuildTime', True):
                    extra_args.append('--generate-partials')
                if releaseConfig.get('l10nUsePymake', True) and \
                   platform in ('win32', 'win64'):
                    extra_args.append('--use-pymake')
                if pf.get('tooltool_l10n_manifest_src'):
                    extra_args.extend(['--tooltool-manifest',
                                      pf.get('tooltool_l10n_manifest_src')])
                if pf.get('tooltool_script'):
                    for script in pf['tooltool_script']:
                        extra_args.extend(['--tooltool-script', script])
                for url in branchConfig['tooltool_url_list']:
                    extra_args.extend(['--tooltool-url', url])
                if balrog_api_root:
                    extra_args.extend([
                        "--balrog-api-root", balrog_api_root,
                        "--balrog-username", balrog_username,
                        "--credentials-file", "oauth.txt",
                    ])
                standalone_factory = SigningScriptFactory(
                    signingServers=getSigningServers(platform),
                    env=env,
                    scriptRepo=tools_repo,
                    interpreter='bash',
                    scriptName='scripts/l10n/release_repacks.sh',
                    extra_args=extra_args,
                    use_mock=use_mock(platform),
                    mock_target=pf.get('mock_target'),
                    mock_packages=pf.get('mock_packages'),
                    mock_copyin_files=pf.get('mock_copyin_files'),
                    use_credentials_file=True,
                    copy_properties=['buildid'],
                )
                # TODO: how to make this work with balrog, where we need 4 properties
                # set (but webstatus only allows for 3).
                # Can we avoid the need for script_repo_revision or release_tag?
                builders.append({
                    'name': builderPrefix("standalone_repack", platform),
                    'slavenames': pf.get('l10n_slaves', pf['slaves']),
                    'category': builderPrefix(''),
                    'builddir': builderPrefix("standalone_repack", platform),
                    'slavebuilddir': normalizeName(builderPrefix(
                        'standalone_repack', platform), releaseConfig['productName']),
                    'factory': standalone_factory,
                    'nextSlave': _nextSlave_skip_spot,
                    'env': env,
                    'properties': {
                        'builddir': builderPrefix("standalone_repack", platform),
                        'slavebuilddir': normalizeName(builderPrefix(
                            "standalone_repack", platform), releaseConfig['productName']),
                        'platform': platform,
                        'branch': 'release-%s' % sourceRepoInfo['name'],
                        'release_config': releaseConfigFile,
                    }
                })

            for n, builderName in l10nBuilders(platform).iteritems():
                builddir = builderPrefix('%s_repack' % platform) + '_' + str(n)
                properties = {
                    'builddir': builddir,
                    'slavebuilddir': normalizeName(builddir, releaseConfig['productName']),
                    'release_config': releaseConfigFile,
                    'platform': platform,
                    'branch': 'release-%s' % sourceRepoInfo['name'],
                    'chunkTotal': int(l10nChunks),
                    'chunkNum': int(n),
                    'event_group': 'repack',
                }
                if hasPlatformSubstring(platform, 'android'):
                    extra_args = releaseConfig['single_locale_options'][platform] + ['--cfg', branchConfig['mozharness_configs']['balrog'], '--total-chunks', str(l10nChunks), '--this-chunk', str(n)]
                    repack_factory = SigningScriptFactory(
                        signingServers=getSigningServers(platform),
                        scriptRepo=mozharness_repo,
                        scriptName='scripts/mobile_l10n.py',
                        extra_args=extra_args,
                        use_credentials_file=True,
                        env=env,
                    )
                    properties['script_repo_revision'] = releaseTag
                else:
                    extra_args = [platform, branchConfigFile]
                    extra_args.extend([
                        '--chunks', str(l10nChunks), '--this-chunk', str(n),
                        '--stage-ssh-key', branchConfig['stage_ssh_key'],
                        '--stage-server', branchConfig['stage_server'],
                        '--stage-username', branchConfig['stage_username'],
                        '--hghost', branchConfig['hghost'],
                        '--compare-locales-repo-path',
                        branchConfig['compare_locales_repo_path']
                    ])
                    if releaseConfig.get('l10nUsePymake', True) and \
                       platform in ('win32', 'win64'):
                        extra_args.append('--use-pymake')
                    if releaseConfig.get('enablePartialMarsAtBuildTime', True):
                        extra_args.append('--generate-partials')
                    if pf.get('tooltool_l10n_manifest_src'):
                        extra_args.extend(['--tooltool-manifest',
                            pf.get('tooltool_l10n_manifest_src')])
                    if pf.get('tooltool_script'):
                        for script in pf['tooltool_script']:
                            extra_args.extend(['--tooltool-script', script])
                    for url in branchConfig['tooltool_url_list']:
                        extra_args.extend(['--tooltool-url', url])
                    if balrog_api_root:
                        extra_args.extend([
                            "--balrog-api-root", balrog_api_root,
                            "--balrog-username", balrog_username,
                            "--credentials-file", "oauth.txt",
                        ])
                    repack_factory = SigningScriptFactory(
                        signingServers=getSigningServers(platform),
                        env=env,
                        scriptRepo=tools_repo,
                        interpreter='bash',
                        scriptName='scripts/l10n/release_repacks.sh',
                        extra_args=extra_args,
                        script_timeout=2400,
                        use_mock=use_mock(platform),
                        mock_target=pf.get('mock_target'),
                        mock_packages=pf.get('mock_packages'),
                        mock_copyin_files=pf.get('mock_copyin_files'),
                        use_credentials_file=True,
                        copy_properties=['buildid'],
                    )

                builders.append({
                    'name': builderName,
                    'slavenames': pf.get('l10n_slaves', pf['slaves']),
                    'category': builderPrefix(''),
                    'builddir': builddir,
                    'slavebuilddir': normalizeName(builddir, releaseConfig['productName']),
                    'factory': repack_factory,
                    'nextSlave': _nextSlave_skip_spot,
                    'env': env,
                    'properties': properties,
                })

            builders.append(makeDummyBuilder(
                name=builderPrefix('repack_complete', platform),
                slaves=all_slaves,
                category=builderPrefix(''),
                properties={
                    'platform': platform,
                    'branch': 'release-%s' % sourceRepoInfo['name'],
                },
            ))
            updates_upstream_builders.append(
                builderPrefix('repack_complete', platform))
            deliverables_builders.append(
                builderPrefix('repack_complete', platform))

        if platform in releaseConfig['unittestPlatforms']:
            mochitestLeakThreshold = pf.get('mochitest_leak_threshold', None)
            crashtestLeakThreshold = pf.get('crashtest_leak_threshold', None)
            for suites_name, suites in branchConfig['unittest_suites']:
                # Release builds on mac don't have a11y enabled, do disable the
                # mochitest-a11y test
                if platform.startswith('macosx') and 'mochitest-a11y' in suites:
                    suites = suites[:]
                    suites.remove('mochitest-a11y')

                test_builders.extend(generateTestBuilder(
                    branchConfig, 'release', platform, builderPrefix(
                        "%s_test" % platform),
                    builderPrefix("%s-opt-unittest" % platform),
                    suites_name, suites, mochitestLeakThreshold,
                    crashtestLeakThreshold, category=builderPrefix('')))

    for platform in releaseConfig.get('xulrunnerPlatforms', []):
        pf = branchConfig['platforms'][platform]
        xr_env = pf['env'].copy()
        xr_env['SYMBOL_SERVER_USER'] = branchConfig['stage_username_xulrunner']
        xr_env['SYMBOL_SERVER_PATH'] = branchConfig[
            'symbol_server_xulrunner_path']
        xr_env['SYMBOL_SERVER_SSH_KEY'] = \
            xr_env['SYMBOL_SERVER_SSH_KEY'].replace(branchConfig['stage_ssh_key'],
                                                    branchConfig['stage_ssh_xulrunner_key'])
        # Turn pymake on by default for Windows, and off by default for
        # other platforms.
        if 'win' in platform:
            enable_pymake = pf.get('enable_pymake', True)
        else:
            enable_pymake = pf.get('enable_pymake', False)

        if not releaseConfig.get('skip_build'):
            xulrunner_build_factory = XulrunnerReleaseBuildFactory(
                env=xr_env,
                objdir=pf['platform_objdir'],
                platform=platform,
                hgHost=branchConfig['hghost'],
                repoPath=sourceRepoInfo['path'],
                buildToolsRepoPath=tools_repo_path,
                configRepoPath=branchConfig['config_repo_path'],
                configSubDir=branchConfig['config_subdir'],
                profiledBuild=None,
                mozconfig='%s/%s/xulrunner' % (
                    platform, sourceRepoInfo['name']),
                srcMozconfig=releaseConfig.get(
                    'xulrunner_mozconfigs', {}).get(platform),
                buildRevision=releaseTag,
                stageServer=branchConfig['stage_server'],
                stageUsername=branchConfig['stage_username_xulrunner'],
                stageGroup=branchConfig['stage_group'],
                stageSshKey=branchConfig['stage_ssh_xulrunner_key'],
                stageBasePath=branchConfig['stage_base_path'] + '/xulrunner',
                uploadPackages=True,
                uploadSymbols=True,
                createSnippet=False,
                doCleanup=True,
                # this will clean-up the mac build dirs, but not delete
                # the entire thing
                buildSpace=pf.get(
                    'build_space', branchConfig['default_build_space']),
                productName='xulrunner',
                version=releaseConfig['version'],
                buildNumber=releaseConfig['buildNumber'],
                clobberURL=clobberer_url,
                clobberBranch='release-%s' % sourceRepoInfo['name'],
                packageSDK=True,
                signingServers=getSigningServers(platform),
                partialUpdates=releaseConfig.get('partialUpdates', {}),
                tooltool_manifest_src=pf.get('tooltool_manifest_src', None),
                tooltool_url_list=branchConfig.get('tooltool_url_list', []),
                tooltool_script=pf.get('tooltool_script'),
                use_mock=use_mock(platform),
                mock_target=pf.get('mock_target'),
                mock_packages=pf.get('mock_packages'),
                mock_copyin_files=pf.get('mock_copyin_files'),
                enable_pymake=enable_pymake,
            )
            builders.append({
                'name': builderPrefix('xulrunner_%s_build' % platform),
                'slavenames': pf['slaves'],
                'category': builderPrefix(''),
                'builddir': builderPrefix('xulrunner_%s_build' % platform),
                'slavebuilddir': normalizeName(builderPrefix('xulrunner_%s_build' % platform), releaseConfig['productName']),
                'factory': xulrunner_build_factory,
                'env': builder_env,
                'properties': {
                    'slavebuilddir': normalizeName(builderPrefix('xulrunner_%s_build' % platform), releaseConfig['productName']),
                    'platform': platform,
                    'branch': 'release-%s' % sourceRepoInfo['name'],
                    'product': 'xulrunner',
                }
            })
        else:
            builders.append(makeDummyBuilder(
                name=builderPrefix('xulrunner_%s_build' % platform),
                slaves=all_slaves,
                category=builderPrefix(''),
                properties={
                    'platform': platform,
                    'branch': 'release-%s' % sourceRepoInfo['name'],
                    'product': 'xulrunner',
                },
            ))
        xr_deliverables_builders.append(
            builderPrefix('xulrunner_%s_build' % platform))

    if releaseConfig['doPartnerRepacks']:
        for platform in releaseConfig.get('partnerRepackPlatforms',
                                          releaseConfig['l10nPlatforms']):
            slaves = None
            partner_repack_factory = None
            if releaseConfig.get('partnerRepackConfig', {}).get('use_mozharness'):
                slaves = branchConfig['platforms']['linux']['slaves']
                mh_cfg = releaseConfig[
                    'partnerRepackConfig']['platforms'][platform]
                extra_args = mh_cfg.get(
                    'extra_args', ['--cfg', mh_cfg['config_file']])
                partner_repack_factory = ScriptFactory(
                    scriptRepo=mozharness_repo,
                    scriptName=mh_cfg['script'],
                    extra_args=extra_args,
                    env=builder_env,
                )
            else:
                pr_pf = branchConfig['platforms']['macosx64']
                slaves = pr_pf['slaves']
                repack_params = dict(
                    hgHost=branchConfig['hghost'],
                    repoPath=sourceRepoInfo['path'],
                    buildToolsRepoPath=tools_repo_path,
                    productName=releaseConfig['productName'],
                    version=releaseConfig['version'],
                    buildNumber=releaseConfig['buildNumber'],
                    partnersRepoPath=releaseConfig['partnersRepoPath'],
                    partnersRepoRevision=releaseTag,
                    platformList=[platform],
                    stagingServer=releaseConfig['stagingServer'],
                    stageUsername=branchConfig['stage_username'],
                    stageSshKey=branchConfig['stage_ssh_key'],
                    signingServers=getSigningServers(platform),
                    env=pr_pf['env'],
                )
                partner_repack_factory = PartnerRepackFactory(**repack_params)

            builders.append({
                'name': builderPrefix('partner_repack', platform),
                'slavenames': slaves,
                'category': builderPrefix(''),
                'builddir': builderPrefix('partner_repack', platform),
                'slavebuilddir': normalizeName(builderPrefix(
                    'partner_repack', platform), releaseConfig['productName']),
                'factory': partner_repack_factory,
                'env': builder_env,
                'properties': {
                    'slavebuilddir': normalizeName(builderPrefix('partner_repack', platform), releaseConfig['productName']),
                    'platform': platform,
                    'branch': 'release-%s' % sourceRepoInfo['name'],
                }
            })
            deliverables_builders.append(
                builderPrefix('partner_repack', platform))

    if releaseConfig.get('autoGenerateChecksums', True):
        pf = branchConfig['platforms']['linux']
        env = builder_env.copy()
        env.update(pf['env'])
        checksums_factory = SigningScriptFactory(
            signingServers=getSigningServers('linux'),
            env=env,
            scriptRepo=tools_repo,
            interpreter='bash',
            scriptName='scripts/release/generate-sums.sh',
            extra_args=[
                branchConfigFile, '--product', releaseConfig['productName'],
                '--ssh-user', branchConfig['stage_username'],
                '--ssh-key', branchConfig['stage_ssh_key'],
                '--create-contrib-dirs',
            ],
        )
        builders.append({
            'name': builderPrefix('%s_checksums' % releaseConfig['productName']),
            'slavenames': branchConfig['platforms']['linux']['slaves'] +
            branchConfig['platforms']['linux64']['slaves'],
            'category': builderPrefix(''),
            'builddir': builderPrefix(
                '%s_checksums' % releaseConfig['productName']),
            'slavebuilddir': normalizeName(
                builderPrefix(
                    '%s_checksums' % releaseConfig['productName']), releaseConfig['productName']),
            'factory': checksums_factory,
            'env': builder_env,
            'nextSlave': _nextSlave_skip_spot,
            'properties': {
                'slavebuilddir': normalizeName(builderPrefix(
                    '%s_checksums' % releaseConfig['productName'])),
                'script_repo_revision': releaseTag,
                'release_config': releaseConfigFile,
                'branch': 'release-%s' % sourceRepoInfo['name'],
                'platform': None,
            }
        })
        post_deliverables_builders.append(
            builderPrefix('%s_checksums' % releaseConfig['productName']))
        if releaseConfig.get('xulrunnerPlatforms'):
            xr_checksums_factory = SigningScriptFactory(
                signingServers=getSigningServers('linux'),
                env=env,
                scriptRepo=tools_repo,
                interpreter='bash',
                scriptName='scripts/release/generate-sums.sh',
                extra_args=[
                    branchConfigFile, '--product', 'xulrunner',
                    '--ssh-user', branchConfig['stage_username_xulrunner'],
                    '--ssh-key', branchConfig['stage_ssh_xulrunner_key'],
                ],
            )
            builders.append({
                'name': builderPrefix('xulrunner_checksums'),
                'slavenames': branchConfig['platforms']['linux']['slaves'] +
                branchConfig['platforms']['linux64']['slaves'],
                'category': builderPrefix(''),
                'builddir': builderPrefix('xulrunner_checksums'),
                'slavebuilddir': normalizeName(builderPrefix('xulrunner_checksums')),
                'factory': xr_checksums_factory,
                'env': builder_env,
                'properties': {
                    'slavebuilddir': normalizeName(
                        builderPrefix('xulrunner_checksums')),
                    'script_repo_revision': releaseTag,
                    'release_config': releaseConfigFile,
                    'platform': None,
                    'branch': 'release-%s' % sourceRepoInfo['name'],
                }
            })

    if releaseConfig.get('verifyConfigs') and \
            not releaseConfig.get('skip_updates'):
        pf = branchConfig['platforms']['linux']
        try:
            moz_repo_path = releaseConfig[
                'sourceRepositories']['mozilla']['path']
        except KeyError:
            moz_repo_path = sourceRepoInfo['path']
        balrog_credentials_file = releaseConfig.get('balrog_credentials_file',
            branchConfig.get('balrog_credentials_file', None))
        updates_factory = ReleaseUpdatesFactory(
            hgHost=branchConfig['hghost'],
            repoPath=sourceRepoInfo['path'],
            buildToolsRepoPath=tools_repo_path,
            configRepoPath=branchConfig['config_repo_path'],
            patcherConfig=releaseConfig['patcherConfig'],
            verifyConfigs=releaseConfig['verifyConfigs'],
            appName=releaseConfig['appName'],
            productName=releaseConfig['productName'],
            version=releaseConfig['version'],
            appVersion=releaseConfig['appVersion'],
            baseTag=releaseConfig['baseTag'],
            buildNumber=releaseConfig['buildNumber'],
            partialUpdates=releaseConfig.get('partialUpdates', {}),
            ftpServer=releaseConfig['ftpServer'],
            bouncerServer=releaseConfig['bouncerServer'],
            stagingServer=releaseConfig['stagingServer'],
            stageUsername=branchConfig['stage_username'],
            stageSshKey=branchConfig['stage_ssh_key'],
            ausUser=releaseConfig['ausUser'],
            ausSshKey=releaseConfig['ausSshKey'],
            ausHost=releaseConfig['ausHost'],
            ausServerUrl=releaseConfig['ausServerUrl'],
            hgSshKey=releaseConfig['hgSshKey'],
            hgUsername=releaseConfig['hgUsername'],
            localTestChannel=releaseConfig['localTestChannel'],
            releaseChannel=releaseChannel,
            clobberURL=clobberer_url,
            clobberBranch='release-%s' % sourceRepoInfo['name'],
            releaseNotesUrl=releaseConfig['releaseNotesUrl'],
            testOlderPartials=releaseConfig['testOlderPartials'],
            longVersion=releaseConfig.get('longVersion', None),
            schema=releaseConfig.get('snippetSchema', None),
            useBetaChannelForRelease=releaseConfig.get(
                'useBetaChannelForRelease', False),
            signingServers=getSigningServers('linux'),
            useChecksums=releaseConfig.get(
                'enablePartialMarsAtBuildTime', True),
            mozRepoPath=moz_repo_path,
            env=branchConfig['platforms']['linux']['env'],
            python=branchConfig['platforms']['linux'][
                'env'].get('PYTHON26', 'python'),
            use_mock=use_mock('linux'),
            mock_target=pf.get('mock_target'),
            mock_packages=pf.get('mock_packages'),
            mock_copyin_files=pf.get('mock_copyin_files'),
            promptWaitTime=releaseConfig.get(
                'promptWaitTime', None),
            balrog_api_root=balrog_api_root,
            balrog_username=balrog_username,
            balrog_credentials_file=balrog_credentials_file,
        )

        builders.append({
            'name': builderPrefix('%s_updates' % releaseConfig['productName']),
            'slavenames': branchConfig['platforms']['linux']['slaves'],
            'category': builderPrefix(''),
            'builddir': builderPrefix('%s_updates' % releaseConfig['productName']),
            'slavebuilddir': normalizeName(builderPrefix('%s_updates' % releaseConfig['productName']), releaseConfig['productName']),
            'factory': updates_factory,
            'nextSlave': _nextSlave_skip_spot,
            'env': builder_env,
            'properties': {
                'slavebuilddir': normalizeName(builderPrefix('%s_updates' % releaseConfig['productName']), releaseConfig['productName']),
                'platform': platform,
                'branch': 'release-%s' % sourceRepoInfo['name'],
                'release_config': releaseConfigFile,
                'script_repo_revision': releaseTag,
                'event_group': 'update',
            }
        })
        post_signing_builders.append(builderPrefix('%s_updates' % releaseConfig['productName']))

        # Releases that aren't automatically pushed to mirrors have their
        # updates tested on an internal channel first. For these, we need to
        # send out mail to let people know that it's ready to test.
        if not releaseConfig.get('enableAutomaticPushToMirrors'):
            important_builders.append(builderPrefix('%s_updates' % releaseConfig['productName']))
        if not releaseConfig.get('enablePartialMarsAtBuildTime', True):
            deliverables_builders.append(builderPrefix('%s_updates' % releaseConfig['productName']))

        update_shipping_factory_args = dict(
            scriptRepo=tools_repo,
            use_credentials_file=True,
            interpreter='python',
            scriptName='scripts/updates/balrog-release-shipper.py',
            extra_args=[
                '-b', '%s%s' % (branchConfig['hgurl'], branchConfig['config_repo_path']),
                '-r', releaseConfigFile,
                '-a', balrog_api_root,
                '-u', balrog_username,
                '-c', 'oauth.txt',
                '-p', 'buildprops.json',
            ],
        )
        update_shipping_factory = ScriptFactory(**update_shipping_factory_args)

        builders.append({
            'name': builderPrefix('update_shipping'),
            'slavenames': unix_slaves,
            'category': builderPrefix(''),
            'builddir': builderPrefix('update_shipping'),
            'slavebuilddir': normalizeName(builderPrefix('update_shipping'), releaseConfig['productName']),
            'factory': update_shipping_factory,
            'env': builder_env,
            'nextSlave': _nextSlave_skip_spot,
            'properties': {
                'slavebuilddir': normalizeName(builderPrefix('update_shipping'), releaseConfig['productName']),
                'release_config': releaseConfigFile,
                'script_repo_revision': releaseTag,
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
            },
        })
        important_builders.append(builderPrefix('update_shipping'))

    elif releaseConfig.get('verifyConfigs') or \
        hasPlatformSubstring(releaseConfig['enUSPlatforms'], 'android'):
        builders.append(makeDummyBuilder(
            name=builderPrefix('%s_updates' % releaseConfig['productName']),
            slaves=all_slaves,
            category=builderPrefix(''),
            properties={
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
                'event_group': 'update',
            },
        ))
        post_signing_builders.append(builderPrefix('%s_updates' % releaseConfig['productName']))

    for platform in sorted(releaseConfig.get('verifyConfigs', {}).keys()):
        vpf = branchConfig['platforms'][platform]
        for n, builderName in updateVerifyBuilders(platform).iteritems():
            uv_factory = ScriptFactory(
                scriptRepo=tools_repo,
                interpreter='bash',
                scriptName='scripts/release/updates/chunked-verify.sh',
                extra_args=[platform, 'verifyConfigs',
                            str(updateVerifyChunks), str(n)],
                log_eval_func=lambda c, s: regex_log_evaluator(
                    c, s, update_verify_error),
                use_mock=use_mock(platform),
                mock_target=vpf.get('mock_target'),
                mock_packages=vpf.get('mock_packages'),
                mock_copyin_files=vpf.get('mock_copyin_files'),
                env=branchConfig['platforms'][platform]['env'],
            )

            builddir = builderPrefix('%s_update_verify' % platform) + \
                '_' + str(n)
            env = builder_env.copy()
            env.update(branchConfig['platforms'][platform]['env'])

            builders.append({
                'name': builderName,
                'slavenames': branchConfig['platforms'][platform]['slaves'],
                'category': builderPrefix(''),
                'builddir': builddir,
                'slavebuilddir': normalizeName(builddir, releaseConfig['productName']),
                'factory': uv_factory,
                'env': env,
                'properties': {'builddir': builddir,
                               'slavebuilddir': normalizeName(builddir, releaseConfig['productName']),
                               'script_repo_revision': runtimeTag,
                               'release_tag': releaseTag,
                               'release_config': releaseConfigFile,
                               'platform': platform,
                               'branch': 'release-%s' % sourceRepoInfo['name'],
                               'chunkTotal': int(updateVerifyChunks),
                               'chunkNum': int(n),
                               'event_group': 'update_verify',
                               },
            })
            post_update_builders.append(builderName)

    if not releaseConfig.get('disablePermissionCheck'):
        check_permissions_factory = ScriptFactory(
            scriptRepo=tools_repo,
            script_timeout=3 * 60 * 60,
            scriptName='scripts/release/stage-tasks.sh',
            extra_args=['permissions',
                        '--extra-excludes=*.zip',
                        '--extra-excludes=*.zip.asc',
                        '--ssh-user', branchConfig['stage_username'],
                        '--ssh-key', branchConfig['stage_ssh_key'],
                        ],
            log_eval_func=lambda c, s: regex_log_evaluator(
                c, s, permission_check_error),
        )

        builders.append({
            'name': builderPrefix('check_permissions'),
            'slavenames': unix_slaves,
            'category': builderPrefix(''),
            'builddir': builderPrefix('check_permissions'),
            'slavebuilddir': normalizeName(builderPrefix('chk_prms'), releaseConfig['productName']),
            'factory': check_permissions_factory,
            'env': builder_env,
            'properties': {'slavebuilddir': normalizeName(builderPrefix('chk_prms'), releaseConfig['productName']),
                           'script_repo_revision': releaseTag,
                           'release_config': releaseConfigFile,
                           'platform': None,
                           'branch': 'release-%s' % sourceRepoInfo['name'],
                           },
        })
        post_deliverables_builders.append(builderPrefix('check_permissions'))

    if not releaseConfig.get('disableVirusCheck'):
        antivirus_factory = ScriptFactory(
            scriptRepo=tools_repo,
            script_timeout=3 * 60 * 60,
            scriptName='scripts/release/stage-tasks.sh',
            extra_args=['antivirus',
                        '--ssh-user', branchConfig['stage_username'],
                        '--ssh-key', branchConfig['stage_ssh_key'],
                        ],
        )

        builders.append({
            'name': builderPrefix('antivirus'),
            'slavenames': unix_slaves,
            'category': builderPrefix(''),
            'builddir': builderPrefix('antivirus'),
            'slavebuilddir': normalizeName(builderPrefix('av'), releaseConfig['productName']),
            'factory': antivirus_factory,
            'env': builder_env,
            'nextSlave': _nextSlave_skip_spot,
            'properties': {'slavebuilddir': normalizeName(builderPrefix('av'), releaseConfig['productName']),
                           'script_repo_revision': releaseTag,
                           'release_config': releaseConfigFile,
                           'platform': None,
                           'branch': 'release-%s' % sourceRepoInfo['name'],
                           }
        })
        post_deliverables_builders.append(builderPrefix('antivirus'))

    push_to_mirrors_factory = ScriptFactory(
        scriptRepo=tools_repo,
        script_timeout=3 * 60 * 60,
        scriptName='scripts/release/stage-tasks.sh',
        extra_args=['push',
                    '--extra-excludes=*.zip',
                    '--extra-excludes=*.zip.asc',
                    '--ssh-user', branchConfig['stage_username'],
                    '--ssh-key', branchConfig['stage_ssh_key'],
                    ],
    )

    builders.append({
        'name': builderPrefix('%s_push_to_mirrors' % releaseConfig['productName']),
        'slavenames': unix_slaves,
        'category': builderPrefix(''),
        'builddir': builderPrefix('%s_push_to_mirrors' % releaseConfig['productName']),
        'slavebuilddir': normalizeName(builderPrefix('psh_mrrrs'), releaseConfig['productName']),
        'factory': push_to_mirrors_factory,
        'env': builder_env,
        'nextSlave': _nextSlave_skip_spot,
        'properties': {
            'slavebuilddir': normalizeName(builderPrefix('psh_mrrrs'), releaseConfig['productName']),
            'release_config': releaseConfigFile,
            'script_repo_revision': releaseTag,
            'platform': None,
            'branch': 'release-%s' % sourceRepoInfo['name'],
        },
    })

    postrelease_factory_args = dict(
        scriptRepo=tools_repo,
        use_credentials_file=True,
        scriptName='scripts/release/stage-tasks.sh',
        extra_args=['postrelease',
                    '--ssh-user', branchConfig['stage_username'],
                    '--ssh-key', branchConfig['stage_ssh_key'],
                    ],
    )
    if releaseConfig.get('xulrunnerPlatforms'):
        postrelease_factory_args["triggered_schedulers"] = [builderPrefix('xr_postrelease')]
    postrelease_factory = ScriptFactory(**postrelease_factory_args)

    builders.append({
        'name': builderPrefix('%s_postrelease' % releaseConfig['productName']),
        'slavenames': unix_slaves,
        'category': builderPrefix(''),
        'builddir': builderPrefix('%s_postrelease' % releaseConfig['productName']),
        'slavebuilddir': normalizeName(builderPrefix('%s_postrelease' % releaseConfig['productName']), releaseConfig['productName']),
        'factory': postrelease_factory,
        'env': builder_env,
        'nextSlave': _nextSlave_skip_spot,
        'properties': {
            'slavebuilddir': normalizeName(builderPrefix('%s_postrelease' % releaseConfig['productName']), releaseConfig['productName']),
            'release_config': releaseConfigFile,
            'script_repo_revision': releaseTag,
            'platform': None,
            'branch': 'release-%s' % sourceRepoInfo['name'],
        },
    })

    if releaseConfig.get('xulrunnerPlatforms'):
        xr_push_to_mirrors_factory = ScriptFactory(
            scriptRepo=tools_repo,
            scriptName='scripts/release/stage-tasks.sh',
            extra_args=[
                'push',
                '--product', 'xulrunner',
                '--ssh-user', branchConfig['stage_username_xulrunner'],
                '--ssh-key', branchConfig['stage_ssh_xulrunner_key'],
                '--overwrite',
            ],
            script_timeout=3 * 60 * 60,
        )
        builders.append({
            'name': builderPrefix('xulrunner_push_to_mirrors'),
            'slavenames': unix_slaves,
            'category': builderPrefix(''),
            'builddir': builderPrefix('xulrunner_push_to_mirrors'),
            'slavebuilddir': normalizeName(builderPrefix('xr_psh_mrrrs')),
            'factory': xr_push_to_mirrors_factory,
            'env': builder_env,
            'nextSlave': _nextSlave_skip_spot,
            'properties': {
                'slavebuilddir': normalizeName(builderPrefix('xr_psh_mrrrs')),
                'release_config': releaseConfigFile,
                'script_repo_revision': releaseTag,
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
            },
        })

        xr_postrelease_factory = ScriptFactory(
            scriptRepo=tools_repo,
            scriptName='scripts/release/stage-tasks.sh',
            extra_args=['postrelease',
                        '--product', 'xulrunner',
                        '--ssh-user', branchConfig['stage_username_xulrunner'],
                        '--ssh-key', branchConfig['stage_ssh_xulrunner_key'],
                        ],
        )

        builders.append({
            'name': builderPrefix('xr_postrelease'),
            'slavenames': unix_slaves,
            'category': builderPrefix(''),
            'builddir': builderPrefix('xr_postrelease'),
            'slavebuilddir': normalizeName(builderPrefix('xr_postrelease')),
            'factory': xr_postrelease_factory,
            'env': builder_env,
            'nextSlave': _nextSlave_skip_spot,
            'properties': {
                'slavebuilddir': normalizeName(builderPrefix('xr_postrelease')),
                'release_config': releaseConfigFile,
                'script_repo_revision': releaseTag,
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
            },
        })

    if not releaseConfig.get('disableBouncerEntries'):
        trigger_uptake_factory = BuildFactory()
        schedulerNames = [builderPrefix('%s_almost-ready-for-release' % releaseConfig['productName'])]
        if releaseConfig.get('verifyConfigs'):
            schedulerNames.append(builderPrefix('ready-for-rel-test'))
        trigger_uptake_factory.addStep(Trigger(
            schedulerNames=schedulerNames,
            set_properties={
                'release_config': releaseConfigFile,
                'script_repo_revision': releaseTag,
            },
        ))
        builders.append({
            'name': builderPrefix('%s_start_uptake_monitoring' % releaseConfig['productName']),
            'slavenames': all_slaves,
            'category': builderPrefix(''),
            'builddir': builderPrefix('%s_start_uptake_monitoring' % releaseConfig['productName']),
            'slavebuilddir': normalizeName(builderPrefix('st_uptake'), releaseConfig['productName']),
            'factory': trigger_uptake_factory,
            'env': builder_env,
            'nextSlave': _nextSlave_skip_spot,
            'properties': {
                'slavebuilddir': normalizeName(builderPrefix('st_uptake'), releaseConfig['productName']),
                'release_config': releaseConfigFile,
                'script_repo_revision': releaseTag,
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
            },
        })

    if releaseConfig.get('verifyConfigs'):
        final_verification_factory = ReleaseFinalVerification(
            hgHost=branchConfig['hghost'],
            buildToolsRepoPath=tools_repo_path,
            verifyConfigs=releaseConfig['verifyConfigs'],
            clobberURL=clobberer_url,
            clobberBranch='release-%s' % sourceRepoInfo['name'],
            repoPath=sourceRepoInfo['path'],
        )

        builders.append({
            'name': builderPrefix('final_verification'),
            'slavenames': branchConfig['platforms']['linux']['slaves'] +
            branchConfig['platforms']['linux64']['slaves'],
            'category': builderPrefix(''),
            'builddir': builderPrefix('final_verification'),
            'slavebuilddir': normalizeName(builderPrefix('fnl_verf'), releaseConfig['productName']),
            'factory': final_verification_factory,
            'env': builder_env,
            'properties': {
                'slavebuilddir': normalizeName(builderPrefix('fnl_verf'), releaseConfig['productName']),
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
            },
        })

    if not releaseConfig.get('disableBouncerEntries'):
        builders.append(makeDummyBuilder(
            name=builderPrefix('%s_ready_for_releasetest_testing' % releaseConfig['productName']),
            slaves=all_slaves,
            category=builderPrefix(''),
            properties={
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
                'event_group': 'releasetest',
            },
        ))
        important_builders.append(
            builderPrefix('%s_ready_for_releasetest_testing' % releaseConfig['productName']))

        builders.append(makeDummyBuilder(
            name=builderPrefix('%s_almost_ready_for_release' % releaseConfig['productName']),
            slaves=all_slaves,
            category=builderPrefix(''),
            properties={
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
            },
        ))

        builders.append(makeDummyBuilder(
            name=builderPrefix('%s_ready_for_release' % releaseConfig['productName']),
            slaves=all_slaves,
            category=builderPrefix(''),
            properties={
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
                'event_group': 'release',
            },
        ))
        important_builders.append(builderPrefix('%s_ready_for_release' % releaseConfig['productName']))

    if not releaseConfig.get('disableBouncerEntries'):
        extra_args = ["-c", releaseConfig["bouncer_submitter_config"],
                      "--revision", releaseTag,
                      "--repo", sourceRepoInfo['path'],
                      "--version", releaseConfig['version'],
                      "--credentials-file", "oauth.txt",
                      "--bouncer-api-prefix", releaseConfig['tuxedoServerUrl'],
                      ]
        for partial in releaseConfig.get('partialUpdates', {}).iterkeys():
            extra_args.extend(["--previous-version", partial])

        bouncer_submitter_factory = ScriptFactory(
            scriptRepo=mozharness_repo,
            scriptName="scripts/bouncer_submitter.py",
            extra_args=extra_args,
            use_credentials_file=True,
        )

        builders.append({
            'name': builderPrefix('%s_bouncer_submitter' % releaseConfig['productName'] ),
            'slavenames': branchConfig['platforms']['linux']['slaves'] +
            branchConfig['platforms']['linux64']['slaves'],
            'category': builderPrefix(''),
            'builddir': builderPrefix('%s_bouncer_submitter' % releaseConfig['productName']),
            'slavebuilddir': normalizeName(builderPrefix('bncr_sub'), releaseConfig['productName']),
            'factory': bouncer_submitter_factory,
            'env': builder_env,
            'nextSlave': _nextSlave_skip_spot,
            'properties': {
                'slavebuilddir': normalizeName(builderPrefix('bncr_sub'), releaseConfig['productName']),
                'release_config': releaseConfigFile,
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
            }
        })

    # Change sources and Schedulers #

    reset_schedulers_scheduler = Scheduler(
        name=builderPrefix(
            '%s_reset_schedulers' % releaseConfig['productName']),
        branch=sourceRepoInfo['path'],
        treeStableTimer=None,
        builderNames=[builderPrefix(
            '%s_reset_schedulers' % releaseConfig['productName'])],
        fileIsImportant=lambda c: changeContainsProduct(c,
                                                        releaseConfig['productName']) \
            and changeBaseTagContainsScriptRepoRevision(c, releaseConfig['baseTag']),
    )
    schedulers.append(reset_schedulers_scheduler)
    if releaseConfig.get('enable_repo_setup'):
        repo_setup_scheduler = Dependent(
            name=builderPrefix('%s_repo_setup' % releaseConfig['productName']),
            upstream=reset_schedulers_scheduler,
            builderNames=[builderPrefix(
                '%s_repo_setup' % releaseConfig['productName'])],
        )
        schedulers.append(repo_setup_scheduler)
        tag_scheduler = Dependent(
            name=builderPrefix('%s_tag' % releaseConfig['productName']),
            upstream=repo_setup_scheduler,
            builderNames=[builderPrefix(
                '%s_tag' % releaseConfig['productName'])],
        )
    else:
        tag_scheduler = Dependent(
            name=builderPrefix('%s_tag' % releaseConfig['productName']),
            upstream=reset_schedulers_scheduler,
            builderNames=[builderPrefix(
                '%s_tag' % releaseConfig['productName'])],
        )
    schedulers.append(tag_scheduler)

    tag_downstream = [builderPrefix('%s_source' % releaseConfig[
                                    'productName'])]

    if releaseConfig['buildNumber'] == 1 \
            and not releaseConfig.get('disableBouncerEntries'):
        tag_downstream.append(builderPrefix('%s_bouncer_submitter' % releaseConfig['productName']))

    if releaseConfig.get('xulrunnerPlatforms'):
        tag_downstream.append(builderPrefix('xulrunner_source'))
        xr_postrelease_scheduler = Triggerable(
            name=builderPrefix('xr_postrelease'),
            builderNames=[builderPrefix('xr_postrelease')],
        )
        schedulers.append(xr_postrelease_scheduler)

    for platform in releaseConfig['enUSPlatforms']:
        tag_downstream.append(builderPrefix('%s_build' % platform))
        if platform in releaseConfig['notifyPlatforms']:
            important_builders.append(builderPrefix('%s_build' % platform))
        if platform in releaseConfig['l10nPlatforms']:
            if platform in releaseConfig.get('l10nNotifyPlatforms', []):
                important_builders.append(builderPrefix('%s_repack_complete' % platform))
            l10nBuilderNames = l10nBuilders(platform).values()
            repack_scheduler = Triggerable(
                name=builderPrefix('%s_repack' % platform),
                builderNames=l10nBuilderNames,
            )
            schedulers.append(repack_scheduler)
            repack_complete_scheduler = AggregatingScheduler(
                name=builderPrefix('%s_repack_complete' % platform),
                branch=sourceRepoInfo['path'],
                upstreamBuilders=l10nBuilderNames,
                builderNames=[builderPrefix('repack_complete', platform), ]
            )
            schedulers.append(repack_complete_scheduler)

    for platform in releaseConfig.get('xulrunnerPlatforms', []):
        tag_downstream.append(builderPrefix('xulrunner_%s_build' % platform))

    DependentID = makePropertiesScheduler(
        Dependent, [buildIDSchedFunc, buildUIDSchedFunc])

    schedulers.append(
        DependentID(
            name=builderPrefix('%s_build' % releaseConfig['productName']),
            upstream=tag_scheduler,
            builderNames=tag_downstream,
        ))

    for platform in releaseConfig['unittestPlatforms']:
        platform_test_builders = []
        for suites_name, suites in branchConfig['unittest_suites']:
            platform_test_builders.extend(
                generateTestBuilderNames(
                    builderPrefix('%s_test' % platform),
                    suites_name, suites))

        s = Scheduler(
            name=builderPrefix('%s-opt-unittest' % platform),
            treeStableTimer=0,
            branch=builderPrefix('%s-opt-unittest' % platform),
            builderNames=platform_test_builders,
        )
        schedulers.append(s)

    if not releaseConfig.get('disableBouncerEntries'):
        readyForReleaseUpstreams = [builderPrefix('%s_almost_ready_for_release' % releaseConfig['productName'])]
        if releaseConfig.get('verifyConfigs'):
            readyForReleaseUpstreams += post_update_builders
            finalVerifyBuilders = []
            if releaseConfig.get('verifyConfigs'):
                finalVerifyBuilders = [builderPrefix('final_verification')]
            readyForReleaseUpstreams += finalVerifyBuilders

            mirror_scheduler1 = TriggerBouncerCheck(
                name=builderPrefix('ready-for-rel-test'),
                configRepo=config_repo,
                minUptake=releaseConfig.get('releasetestUptake', 10000),
                builderNames=[builderPrefix(
                    '%s_ready_for_releasetest_testing' % releaseConfig['productName'])] + finalVerifyBuilders,
                username=BuildSlaves.tuxedoUsername,
                password=BuildSlaves.tuxedoPassword)

            schedulers.append(mirror_scheduler1)

        # These next two schedulers are a bit weird. When updates are enabled,
        # we need to wait for both the update verify builders and the uptake
        # check before we send the "ready for release" e-mail. Because the
        # TriggerBouncerCheck builder can't depend on an upstream, we need the
        # "ready for release" scheduler to be downstream of both it and the
        # update verify builders to get the behaviour we need.
        schedulers.append(TriggerBouncerCheck(
            name=builderPrefix('%s_almost-ready-for-release' % releaseConfig['productName']),
            configRepo=config_repo,
            minUptake=releaseConfig.get('releaseUptake', 10000),
            checkMARs=not releaseConfig.get('skip_updates', False),
            builderNames=[builderPrefix('%s_almost_ready_for_release' % releaseConfig['productName'])],
            username=BuildSlaves.tuxedoUsername,
            password=BuildSlaves.tuxedoPassword
        ))

        schedulers.append(AggregatingScheduler(
            name=builderPrefix('ready-for-release_%s' % releaseConfig['productName']),
            branch=sourceRepoInfo['path'],
            upstreamBuilders=readyForReleaseUpstreams,
            builderNames=[builderPrefix('%s_ready_for_release' % releaseConfig['productName'])],
        ))

    if releaseConfig.get('enableAutomaticPushToMirrors') and \
            releaseConfig.get('verifyConfigs'):
        if releaseConfig.get('disableVirusCheck'):
            post_update_builders.append(builderPrefix('%s_push_to_mirrors' % releaseConfig['productName']))
        else:
            post_antivirus_builders.append(builderPrefix('%s_push_to_mirrors' % releaseConfig['productName']))

    if releaseConfig.get('enableAutomaticPushToMirrors') and \
            hasPlatformSubstring(releaseConfig['enUSPlatforms'], 'android'):
        post_deliverables_builders.append(builderPrefix('%s_push_to_mirrors' % releaseConfig['productName']))

    schedulers.append(AggregatingScheduler(
        name=builderPrefix(
            '%s_signing_done' % releaseConfig['productName']),
        branch=sourceRepoInfo['path'],
        upstreamBuilders=updates_upstream_builders,
        builderNames=post_signing_builders,
    ))
    if releaseConfig.get('verifyConfigs'):
        schedulers.append(AggregatingScheduler(
            name=builderPrefix('updates_done'),
            branch=sourceRepoInfo['path'],
            upstreamBuilders=[builderPrefix('%s_updates' % releaseConfig['productName'])],
            builderNames=post_update_builders,
        ))
    if post_deliverables_builders:
        schedulers.append(AggregatingScheduler(
            name=builderPrefix(
                '%s_deliverables_ready' % releaseConfig['productName']),
            branch=sourceRepoInfo['path'],
            upstreamBuilders=deliverables_builders,
            builderNames=post_deliverables_builders,
        ))
    if releaseConfig.get('xulrunnerPlatforms'):
        if xr_deliverables_builders:
            schedulers.append(AggregatingScheduler(
                name=builderPrefix('xulrunner_deliverables_ready'),
                branch=sourceRepoInfo['path'],
                upstreamBuilders=xr_deliverables_builders,
                builderNames=[builderPrefix('xulrunner_checksums')],
            ))
        schedulers.append(AggregatingScheduler(
            name=builderPrefix('xulrunner_push_to_mirrors'),
            branch=sourceRepoInfo['path'],
            upstreamBuilders=[builderPrefix('xulrunner_checksums')],
            builderNames=[builderPrefix('xulrunner_push_to_mirrors')],
        ))
    if post_antivirus_builders:
        schedulers.append(AggregatingScheduler(
            name=builderPrefix('av_done'),
            branch=sourceRepoInfo['path'],
            upstreamBuilders=[builderPrefix('antivirus')],
            builderNames=post_antivirus_builders,
        ))
    if releaseConfig['doPartnerRepacks'] and \
            not hasPlatformSubstring(releaseConfig['enUSPlatforms'], 'android'):
        # TODO: revisit this once we have android partner repacks
        for platform in releaseConfig.get('partnerRepackPlatforms',
                                          releaseConfig['l10nPlatforms']):
            schedulers.append(AggregatingScheduler(
                name=builderPrefix(
                    '%s_l10n_done' % releaseConfig['productName'],
                    platform),
                branch=sourceRepoInfo['path'],
                upstreamBuilders=[builderPrefix('repack_complete', platform)],
                builderNames=[builderPrefix('partner_repack', platform)],
            ))
    upstream_builders = [builderPrefix('%s_push_to_mirrors' % releaseConfig['productName'])]
    if releaseConfig.get('verifyConfigs'):
        upstream_builders.append(builderPrefix('%s_updates' % releaseConfig['productName']))
    if not releaseConfig.get('disableBouncerEntries'):
        schedulers.append(AggregatingScheduler(
            name=builderPrefix(
                '%s_uptake_check' % releaseConfig['productName']),
            branch=sourceRepoInfo['path'],
            upstreamBuilders=upstream_builders,
            builderNames=[builderPrefix('%s_start_uptake_monitoring' % releaseConfig['productName'])]
        ))

    # This builder should be come after all AggregatingSchedulers are set
    aggregating_shedulers = []
    for s in schedulers:
        if isinstance(s, AggregatingScheduler):
            aggregating_shedulers.append(s.name)
    if aggregating_shedulers:
        scheduler_reset_factory = BuildFactory()
        scheduler_reset_factory.addStep(Trigger(
            schedulerNames=aggregating_shedulers,
        ))
        builders.append({
            'name': builderPrefix(
                '%s_reset_schedulers' % releaseConfig['productName']),
            'slavenames': all_slaves,
            'category': builderPrefix(''),
            'builddir': builderPrefix(
                '%s_reset_schedulers' % releaseConfig['productName']),
            'factory': scheduler_reset_factory,
            'nextSlave': _nextSlave_skip_spot,
            'properties': {
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
            },
        })
    else:
        builders.append(makeDummyBuilder(
            name=builderPrefix(
                '%s_reset_schedulers' % releaseConfig['productName']),
            slaves=all_slaves,
            category=builderPrefix(''),
            properties={
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
            },
        ))

    # Separate email messages per list. Mailman doesn't try to avoid duplicate
    # messages in this case. See Bug 635527 for the details.
    tagging_started_recipients = releaseConfig['AllRecipients'][:]
    if not releaseConfig.get('skip_tag'):
        tagging_started_recipients.extend(releaseConfig['ImportantRecipients'])
    for recipient in tagging_started_recipients:
        # send a message when we receive the sendchange and start tagging
        status.append(ChangeNotifier(
            fromaddr="release@mozilla.com",
            relayhost="mail.build.mozilla.org",
            sendToInterestedUsers=False,
            extraRecipients=[recipient],
            extraHeaders={'Message-Id': email_message_id},
            branches=[sourceRepoInfo['path']],
            messageFormatter=createReleaseChangeMessage,
            changeIsImportant=lambda c:
            changeContainsProduct(c, releaseConfig['productName']) and
            changeBaseTagContainsScriptRepoRevision(c, releaseConfig['baseTag'])
        ))
    for recipient in releaseConfig['ImportantRecipients']:
        if hasPlatformSubstring(releaseConfig['enUSPlatforms'], 'android'):
            # send a message when android signing is complete
            status.append(ChangeNotifier(
                fromaddr="release@mozilla.com",
                relayhost="mail.build.mozilla.org",
                sendToInterestedUsers=False,
                extraRecipients=[recipient],
                extraHeaders={'In-Reply-To': email_message_id,
                              'References': email_message_id},
                branches=[builderPrefix('android_post_signing')],
                messageFormatter=createReleaseChangeMessage,
                changeIsImportant=lambda c:
                changeContainsProperties(c, dict(who=enUS_signed_apk_url))
            ))

    # send the nice(passing) release messages
    status.append(MailNotifier(
        fromaddr='release@mozilla.com',
        sendToInterestedUsers=False,
        extraRecipients=releaseConfig['ImportantRecipients'],
        extraHeaders={'In-Reply-To': email_message_id,
                      'References': email_message_id},
        mode='passing',
        builders=important_builders,
        relayhost='mail.build.mozilla.org',
        messageFormatter=createReleaseMessage,
    ))

    # send all release messages
    status.append(MailNotifier(
        fromaddr='release@mozilla.com',
        sendToInterestedUsers=False,
        extraRecipients=releaseConfig['AllRecipients'],
        extraHeaders={'In-Reply-To': email_message_id,
                      'References': email_message_id},
        mode='all',
        builders=[b['name'] for b in builders + test_builders],
        relayhost='mail.build.mozilla.org',
        messageFormatter=createReleaseMessage,
    ))

    if releaseConfig.get('AVVendorsRecipients'):
        status.append(MailNotifier(
            fromaddr='release@mozilla.com',
            sendToInterestedUsers=False,
            extraRecipients=releaseConfig['AVVendorsRecipients'],
            extraHeaders={'In-Reply-To': email_message_id,
                          'References': email_message_id},
            mode='passing',
            builders=[builderPrefix('%s_updates' % releaseConfig['productName'])],
            relayhost='mail.build.mozilla.org',
            messageFormatter=createReleaseAVVendorsMessage,
        ))

    builders.extend(test_builders)

    # Don't merge release builder requests
    nomergeBuilders.update([b['name'] for b in builders + test_builders])

    # Make sure all builders have our build number and version set
    for b in builders:
        props = b.setdefault('properties', {})
        if 'build_number' not in props:
            props['build_number'] = releaseConfig['buildNumber']
        if 'version' not in props:
            props['version'] = releaseConfig['version']
        if 'product' not in props:
            props['product'] = releaseConfig['productName'].capitalize()

    return {
        "builders": builders,
        "status": status,
        "change_source": change_source,
        "schedulers": schedulers,
    }
