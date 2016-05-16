# The absolute_import directive looks firstly at what packages are available
# on sys.path to avoid name collisions when we import release.* from elsewhere
from __future__ import absolute_import

from collections import defaultdict
import os
import re
import hashlib
from distutils.version import LooseVersion

from buildbot.process.buildstep import regex_log_evaluator
from buildbot.scheduler import Scheduler, Dependent
from buildbot.status.mail import MailNotifier
from buildbot.steps.trigger import Trigger
from buildbot.status.builder import Results
from buildbot.process.factory import BuildFactory

import buildbotcustom.common
import release.platforms
import release.paths
import build.paths
import release.info
reload(buildbotcustom.common)
reload(release.platforms)
reload(release.paths)
reload(build.paths)
reload(release.info)

from buildbotcustom.status.mail import ChangeNotifier
from buildbotcustom.misc import (
    generateTestBuilderNames, generateTestBuilder, changeContainsProduct,
    nomergeBuilders, changeContainsProperties,
    changeContainsScriptRepoRevision, makeMHFactory,
    addBuilderProperties)
from buildbotcustom.common import normalizeName
from buildbotcustom.process.factory import (
    ScriptFactory, SingleSourceFactory, ReleaseBuildFactory,
    ReleaseUpdatesFactory, ReleaseFinalVerification,
    makeDummyBuilder, SigningScriptFactory,
    DummyFactory)
from release.platforms import buildbot2ftp
from release.paths import makeCandidatesDir
from buildbotcustom.scheduler import TriggerBouncerCheck, \
    makePropertiesScheduler, AggregatingScheduler
from buildbotcustom.misc_scheduler import buildIDSchedFunc, buildUIDSchedFunc
from buildbotcustom.status.errors import update_verify_error
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
    # Bug 1179476 - use in gecko tree mozharness for release jobs
    relengapi_archiver_repo_path = sourceRepoInfo['path']
    if sourceRepoKey == "comm":
        relengapi_archiver_repo_path = releaseConfig['sourceRepositories']['mozilla']['path']
    releaseTag = getReleaseTag(releaseConfig['baseTag'])
    # This tag is created post-signing, when we do some additional
    # config file bumps
    runtimeTag = getRuntimeTag(releaseTag)
    l10nChunks = releaseConfig.get('l10nChunks', DEFAULT_PARALLELIZATION)
    updateVerifyChunks = releaseConfig.get(
        'updateVerifyChunks', DEFAULT_PARALLELIZATION)
    # Re-use the same chunking configuration
    ui_update_verify_chunks = releaseConfig.get(
        'updateVerifyChunks', DEFAULT_PARALLELIZATION)
    tools_repo_path = releaseConfig.get('build_tools_repo_path',
                                        branchConfig['build_tools_repo_path'])
    tools_repo = '%s%s' % (branchConfig['hgurl'], tools_repo_path)
    config_repo = '%s%s' % (branchConfig['hgurl'],
                            branchConfig['config_repo_path'])
    mozharness_repo_path = releaseConfig.get('mozharness_repo_path',
                                             branchConfig['mozharness_repo_path'])
    mozharness_repo = '%s%s' % (branchConfig['hgurl'], mozharness_repo_path)
    with_l10n = len(releaseConfig['l10nPlatforms']) > 0 or \
        (releaseConfig.get('enableMultiLocale') and \
        releaseConfig.get('multilocale_config', {}).get('platforms'))
    clobberer_url = releaseConfig.get('base_clobber_url',
                                      branchConfig['base_clobber_url'])
    balrog_api_root = releaseConfig.get('balrog_api_root',
                                        branchConfig.get('balrog_api_root', None))
    balrog_username = releaseConfig.get('balrog_username',
                                        branchConfig.get('balrog_username', None))

    # Despite being a key in the updateChannels dict, we still need this
    # singular release channel to bake in the correct channel when doing
    # builds and repacks.
    releaseChannel = releaseConfig.get("releaseChannel", "release")

    # The updateChannels in the release config need a bit of smoothing before
    # they can be used to set-up builders/schedulers.
    updateChannels = {}
    for channel, config in releaseConfig.get("updateChannels").iteritems():
        # 1) Sometimes they are not enabled, in which case we shouldn't
        # add any builders or schedulers for them. (Eg, point releases).
        if not config.get("enabled", True):
            continue

        updateChannels[channel] = config.copy()
        # 2) The partial updates need to be associated with the correct
        # channels. Eg, partials for the beta channel should not be part of
        # release channel update data.
        partials = {}
        updateChannels[channel]["partialUpdates"] = {}
        for v in releaseConfig["partialUpdates"]:
            if re.match(config.get("versionRegex", ".*"), v):
                partials[v] = releaseConfig["partialUpdates"][v]

        updateChannels[channel]["partialUpdates"] = partials

    branchConfigFile = getRealpath('localconfig.py')
    unix_slaves = []
    mock_slaves = []
    av_slaves = []
    all_slaves = []
    for p in branchConfig['platforms']:
        if p == 'b2g':
            continue
        platform_slaves = branchConfig['platforms'][p].get('slaves', [])
        all_slaves.extend(platform_slaves)
        if 'linux64-av' in p:
            av_slaves.extend(platform_slaves)
        elif 'win' not in p:
            unix_slaves.extend(platform_slaves)
            if branchConfig['platforms'][p].get('use_mock'):
                mock_slaves.extend(platform_slaves)
    unix_slaves = [x for x in set(unix_slaves)]
    mock_slaves = [x for x in set(mock_slaves)]
    av_slaves = [x for x in set(av_slaves)]
    all_slaves = [x for x in set(all_slaves)]

    if secrets is None:
        secrets = {}

    def getSigningServers(platform):
        signingServers = secrets.get('release-signing')
        assert signingServers, 'Please provide a valid list of signing servers'
        return signingServers

    def builderPrefix(postfix, platform=None):
        if platform:
            return "release-%s-%s_%s" % (sourceRepoInfo['name'], platform, postfix)
        else:
            return "release-%s-%s" % (sourceRepoInfo['name'], postfix)

    def releasePrefix():
        """Construct a standard format product release name from the
           product name, version and build number stored in release_config.py
        """
        return "%s %s build%s" % (
            releaseConfig['productName'].title(),
            releaseConfig['version'],
            releaseConfig['buildNumber'], )

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
        stage = name.replace(builderPrefix(""), "")

        # Detect platform from builder name by tokenizing by '_', and matching
        # the first token after the prefix
        platform = [p for p in allplatforms if stage.split('_')[0] == p]
        platform = platform[0] if len(platform) >= 1 else ''
        bare_platform = platform
        message_tag = getMessageTag()
        buildbot_url = ''
        if master_status.getURLForThing(build):
            buildbot_url = "Full details are available at:\n %s\n" % master_status.getURLForThing(build)
        # Use a generic ftp URL non-specific to any locale
        ftpURL = genericHttpsUrl()
        isPlatformUnsigned = False
        if platform:
            platformDir = buildbot2ftp(bare_platform)
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
        ftpURL = genericHttpsUrl()
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

    def updateVerifyBuilders(platform, channel):
        return parallelizeBuilders("update_verify_%s" % channel, platform,
                                   updateVerifyChunks)

    def ui_update_verify_builders(platform, channel):
        return parallelizeBuilders("ui_update_verify_%s" % channel, platform,
                                   ui_update_verify_chunks)

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
    status = []
    updates_upstream_builders = []
    post_signing_builders = []
    extra_updates_builders = []
    update_verify_builders = defaultdict(list)
    ui_update_tests_builders = defaultdict(list)
    deliverables_builders = []
    post_deliverables_builders = []
    email_message_id = getMessageId()

    # Builders #

    builder_env = {
        'BUILDBOT_CONFIGS': '%s%s' % (branchConfig['hgurl'],
                                      branchConfig['config_repo_path']),
        'BUILDBOTCUSTOM': '%s%s' % (branchConfig['hgurl'],
                                    branchConfig['buildbotcustom_repo_path']),
        'CLOBBERER_URL': clobberer_url,
    }
    # The following variable is used to make buildbot reload dummy builders
    dummy_builder_env = {
        'DUMMY_RELEASE_PREFIX': releasePrefix(),
    }
    if use_mock('linux'):
        unix_slaves = mock_slaves

    dummy_tag_builders = []
    if not releaseConfig.get('skip_tag'):
        pf = branchConfig['platforms']['linux']
        tag_env = builder_env.copy()
        if pf['env'].get('PATH'):
            tag_env['PATH'] = pf['env']['PATH']
        if pf['env'].get('HG_SHARE_BASE_DIR', None):
            tag_env['HG_SHARE_BASE_DIR'] = pf['env']['HG_SHARE_BASE_DIR']

        # Other includes mozharness, required for Mobile Builds
        tag_source_factory = ScriptFactory(
            scriptRepo=tools_repo,
            scriptName='scripts/release/tagging.sh',
            use_mock=use_mock('linux'),
            mock_target=pf.get('mock_target'),
            mock_packages=pf.get('mock_packages'),
            mock_copyin_files=pf.get('mock_copyin_files'),
            env=tag_env,
            extra_data={"tag_args": "--tag-source  --tag-other"}
        )

        builders.append({
            'name': builderPrefix('%s_tag_source' % releaseConfig['productName']),
            'slavenames': pf['slaves'],
            'category': builderPrefix(''),
            'builddir': builderPrefix('%s_tag_source' % releaseConfig['productName']),
            'slavebuilddir': normalizeName(
                builderPrefix('%s_tag' % releaseConfig['productName'])),
            'factory': tag_source_factory,
            'env': tag_env,
            'properties': {
                'builddir': builderPrefix(
                    '%s_tag_source' % releaseConfig['productName']),
                'slavebuilddir': normalizeName(
                    builderPrefix('%s_tag_source' % releaseConfig['productName'])),
                'release_config': releaseConfigFile,
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
                'event_group': 'tag',
                'script_repo_revision': releaseTag,
            }
        })

        if with_l10n:
            tag_l10n_factory = ScriptFactory(
                scriptRepo=tools_repo,
                scriptName='scripts/release/tagging.sh',
                use_mock=use_mock('linux'),
                mock_target=pf.get('mock_target'),
                mock_packages=pf.get('mock_packages'),
                mock_copyin_files=pf.get('mock_copyin_files'),
                env=tag_env,
                extra_data={"tag_args": "--tag-l10n"},
            )

            builders.append({
                'name': builderPrefix('%s_tag_l10n' % releaseConfig['productName']),
                'slavenames': pf['slaves'] + branchConfig['platforms']['linux64']['slaves'],
                'category': builderPrefix(''),
                'builddir': builderPrefix('%s_tag_l10n' % releaseConfig['productName']),
                'slavebuilddir': normalizeName(
                    builderPrefix('%s_tag_l10n' % releaseConfig['productName'])),
                'factory': tag_l10n_factory,
                'env': tag_env,
                'properties': {
                    'builddir': builderPrefix(
                        '%s_tag_l10n' % releaseConfig['productName']),
                    'slavebuilddir': normalizeName(
                        builderPrefix('%s_tag_l10n' % releaseConfig['productName'])),
                    'release_config': releaseConfigFile,
                    'platform': None,
                    'branch': 'release-%s' % sourceRepoInfo['name'],
                    'script_repo_revision': releaseTag,
                }
            })
        else:
            dummy_tag_builders.append("l10n")
    else:
        dummy_tag_builders.extend(["source", "l10n"])
        for dummy in dummy_tag_builders:
            builders.append(makeDummyBuilder(
                            name=builderPrefix('%s_tag_%s' %
                                               (releaseConfig['productName'], dummy)),
                            slaves=all_slaves,
                            category=builderPrefix(''),
                            properties={
                                'platform': None,
                                'branch': 'release-%s' % sourceRepoInfo['name'],
                            },
                            env=dummy_builder_env,
                            ))

    if not releaseConfig.get('skip_source'):
        pf = branchConfig['platforms']['linux64']
        # Everywhere except Thunderbird we use browser mozconfigs to generate
        # source tarballs. This includes Android
        mozconfig = releaseConfig.get(
            'source_mozconfig',
            'browser/config/mozconfigs/linux64/release')
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
            signingServers=getSigningServers('linux'),
            use_mock=source_use_mock,
            mock_target=pf.get('mock_target'),
            mock_packages=pf.get('mock_packages'),
            mock_copyin_files=pf.get('mock_copyin_files'),
            bucketPrefix=branchConfig.get('bucket_prefix'),
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

    else:
        builders.append(makeDummyBuilder(
            name=builderPrefix('%s_source' % releaseConfig['productName']),
            slaves=all_slaves,
            category=builderPrefix(''),
            properties={
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
            },
            env=dummy_builder_env,
        ))

    mozillaDir = None
    mozillaSrcDir = None
    if 'mozilla_dir' in releaseConfig:
        mozillaDir = releaseConfig['mozilla_dir']
    if 'mozilla_srcdir' in releaseConfig:
        mozillaSrcDir = releaseConfig['mozilla_srcdir']

    partialUpdates = releaseConfig.get('partialUpdates', {}).copy()

    for platform in releaseConfig['enUSPlatforms']:
        # FIXME: the follwong hack can be removed when win64 has the same list
        # of partial update as other platforms. Check mozilla-esr38 to be sure.
        if platform in releaseConfig.get('HACK_first_released_version', {}):
            partialUpdates_hacked = {
                k: v for k, v in partialUpdates.iteritems() if
                LooseVersion(k) >= LooseVersion(releaseConfig['HACK_first_released_version'][platform])
            }
        else:
            partialUpdates_hacked = partialUpdates
        # FIXME: end of hack
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
                uploadSymbols=not branchConfig.get('staging', False),
                doCleanup=True,
                # this will clean-up the mac build dirs, but not delete
                # the entire thing
                buildSpace=pf.get(
                    'build_space', branchConfig['default_build_space']),
                productName=releaseConfig['productName'],
                version=releaseConfig['version'],
                appVersion=releaseConfig['appVersion'],
                buildNumber=releaseConfig['buildNumber'],
                partialUpdates=partialUpdates_hacked,  # FIXME: hack
                talosMasters=talosMasters,
                packageTests=packageTests,
                packageSDK=releaseConfig.get('packageSDK', False),
                unittestMasters=unittestMasters,
                unittestBranch=unittestBranch,
                clobberURL=clobberer_url,
                clobberBranch='release-%s' % sourceRepoInfo['name'],
                triggerBuilds=True,
                stagePlatform=buildbot2ftp(platform),
                multiLocale=bool(releaseConfig.get('enableMultiLocale', False) and
                                 pf.get('multi_locale', False)),
                multiLocaleMerge=releaseConfig.get('mergeLocales', False),
                compareLocalesRepoPath=branchConfig[
                    'compare_locales_repo_path'],
                # mozharnessRepoPath and mozharnessTag are legacy.
                # tracking the deletion of these is  managed in https://bugzil.la/1180060
                mozharnessRepoPath=mozharness_repo_path,
                mozharnessTag=releaseTag,
                # normally archiver gets Mozharness from repoPath which normally points to a ff
                # gecko repo. But for Thunderbird, repoPath points to a comm- repo. Here we will
                # pass an explicit repo for archiver to use
                relengapi_archiver_repo_path=relengapi_archiver_repo_path,
                relengapi_archiver_release_tag=releaseTag,
                multiLocaleScript=pf.get('multi_locale_script'),
                multiLocaleConfig=multiLocaleConfig,
                mozharnessMultiOptions=mozharnessMultiOptions,
                usePrettyNames=releaseConfig.get('usePrettyNames', True),
                enableUpdatePackaging=releaseConfig.get(
                    'enableUpdatePackaging', True),
                signingServers=getSigningServers(platform),
                createPartial=releaseConfig.get(
                    'enablePartialMarsAtBuildTime', True),
                mozillaDir=mozillaDir,
                mozillaSrcDir=mozillaSrcDir,
                enableInstaller=pf.get('enable_installer', False),
                tooltool_manifest_src=pf.get('tooltool_manifest_src'),
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
                bucketPrefix=branchConfig.get('bucket_prefix'),
                ftpServer=releaseConfig['ftpServer'],
            )

            builders.append({
                'name': builderPrefix('%s_build' % platform),
                'slavenames': pf['slaves'],
                'category': builderPrefix(''),
                'builddir': builderPrefix('%s_build' % platform),
                'slavebuilddir': normalizeName(builderPrefix('%s_build' % platform), releaseConfig['productName']),
                'factory': build_factory,
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
                env=dummy_builder_env,
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
                    '--ftp-server', releaseConfig['ftpServer'],
                    '--hghost', branchConfig['hghost'],
                    '--compare-locales-repo-path',
                    branchConfig['compare_locales_repo_path']
                ])
                if releaseConfig.get('enablePartialMarsAtBuildTime', True):
                    extra_args.append('--generate-partials')
                if releaseConfig.get('l10nUsePymake', True) and \
                   platform in ('win32', 'win64'):
                    extra_args.append('--use-pymake')
                if pf.get('tooltool_manifest_src'):
                    extra_args.extend(['--tooltool-manifest',
                                      pf.get('tooltool_manifest_src')])
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
                    script_timeout=2400,
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
                    'script_repo_revision': releaseTag,
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
                        relengapi_archiver_repo_path=relengapi_archiver_repo_path,
                        relengapi_archiver_release_tag=releaseTag,
                    )
                else:
                    extra_args = [platform, branchConfigFile]
                    extra_args.extend([
                        '--chunks', str(l10nChunks), '--this-chunk', str(n),
                        '--stage-ssh-key', branchConfig['stage_ssh_key'],
                        '--stage-server', branchConfig['stage_server'],
                        '--stage-username', branchConfig['stage_username'],
                        '--ftp-server', releaseConfig['ftpServer'],
                        '--hghost', branchConfig['hghost'],
                        '--compare-locales-repo-path',
                        branchConfig['compare_locales_repo_path']
                    ])
                    if releaseConfig.get('l10nUsePymake', True) and \
                       platform in ('win32', 'win64'):
                        extra_args.append('--use-pymake')
                    if releaseConfig.get('enablePartialMarsAtBuildTime', True):
                        extra_args.append('--generate-partials')
                    if pf.get('tooltool_manifest_src'):
                        extra_args.extend(['--tooltool-manifest',
                                          pf.get('tooltool_manifest_src')])
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
                    if branchConfig.get('bucket_prefix'):
                        extra_args.extend([
                            '--bucket-prefix', branchConfig.get('bucket_prefix'),
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
                    'event_group': 'repack',
                },
                env=dummy_builder_env,
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

    if releaseConfig['doPartnerRepacks']:
        for platform in releaseConfig.get('partnerRepackPlatforms',
                                          releaseConfig['l10nPlatforms']):
            slaves = None
            partner_repack_factory = None
            if releaseConfig['productName'] == 'fennec':
                mh_cfg = releaseConfig['partnerRepackConfig']['platforms'][platform]
                extra_args = mh_cfg.get('extra_args', ['--cfg', mh_cfg['config_file']])
                slaves = branchConfig['platforms']['linux']['slaves']
                partner_repack_factory = ScriptFactory(
                    scriptRepo=mozharness_repo,
                    scriptName=mh_cfg['script'],
                    extra_args=extra_args,
                    env=builder_env,
                    relengapi_archiver_repo_path=relengapi_archiver_repo_path,
                    relengapi_archiver_release_tag=releaseTag,
                )
            else:
                mh_cfg = releaseConfig['partnerRepackConfig']
                extra_args = mh_cfg.get('extra_args', ['--cfg', mh_cfg['config_file']])
                extra_args.extend([
                        "--version", releaseConfig["version"],
                        "--build-number", releaseConfig["buildNumber"],
                        "--platform", platform,
                        "--s3cfg", mh_cfg['s3cfg'],
                        "--hgrepo", releaseConfig['sourceRepositories']['mozilla']['path'],
                        ])
                slaves = branchConfig['platforms']['macosx64']['slaves']
                partner_repack_factory = SigningScriptFactory(
                    signingServers=getSigningServers(platform),
                    scriptRepo=mozharness_repo,
                    interpreter="python2.7",
                    scriptName=mh_cfg['script'],
                    extra_args=extra_args,
                    relengapi_archiver_repo_path=relengapi_archiver_repo_path,
                    relengapi_archiver_release_tag=releaseTag,
                    tools_repo_cache=branchConfig["platforms"]["macosx64"]["tools_repo_cache"],
                )

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

        mh_cfg = releaseConfig['partnerRepackConfig']
        if releaseConfig['productName'] != 'fennec' and mh_cfg.get('config_file', False):
            platform = 'macosx64'
            slaves = branchConfig['platforms'][platform]['slaves']
            extra_args = mh_cfg.get('extra_args', ['--cfg', mh_cfg.get('config_file')])
            extra_args.extend([
                    "--version", releaseConfig["version"],
                    "--build-number", releaseConfig["buildNumber"],
                    "--s3cfg", mh_cfg['s3cfg'],
                    "--require-buildprops",
                    ])
            standalone_partner_repack_factory = SigningScriptFactory(
                signingServers=getSigningServers(platform),
                scriptRepo=mozharness_repo,
                interpreter="python2.7",
                scriptName=mh_cfg['script'],
                extra_args=extra_args,
                relengapi_archiver_repo_path=relengapi_archiver_repo_path,
                relengapi_archiver_release_tag=releaseTag,
                tools_repo_cache=branchConfig["platforms"][platform]["tools_repo_cache"],
            )

            builders.append({
                'name': builderPrefix('standalone_partner_repack'),
                'slavenames': slaves,
                'category': builderPrefix(''),
                'builddir': builderPrefix('standalone_partner_repack'),
                'slavebuilddir': normalizeName(builderPrefix(
                    'partner_repack', platform), releaseConfig['productName']),
                'factory': standalone_partner_repack_factory,
                'env': builder_env,
                'properties': {
                    'slavebuilddir': normalizeName(builderPrefix('standalone_partner_repack'), releaseConfig['productName']),
                    'platform': platform,
                'branch': 'release-%s' % sourceRepoInfo['name'],
                }
            })

    if releaseConfig.get('autoGenerateChecksums', True):
        extra_extra_args = []
        if releaseConfig['productName'] == 'fennec':
            extra_extra_args = ['--add-action=copy-info-files']
        checksums_factory = SigningScriptFactory(
            signingServers=getSigningServers('linux'),
            scriptRepo=mozharness_repo,
            interpreter="python2.7",
            scriptName='scripts/release/generate-checksums.py',
            extra_args=[
                "--stage-product", releaseConfig["stage_product"],
                "--version", releaseConfig["version"],
                "--build-number", releaseConfig["buildNumber"],
                "--bucket-name-prefix", branchConfig["bucket_prefix"],
                "--credentials", releaseConfig["S3Credentials"],
                "--tools-repo", branchConfig["platforms"]["linux64"]["tools_repo_cache"],
            ] + extra_extra_args,
            relengapi_archiver_repo_path=relengapi_archiver_repo_path,
            relengapi_archiver_release_tag=releaseTag,
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
    for channel, updateConfig in updateChannels.iteritems():
        # If updates are fully disabled we should bail completely
        if releaseConfig.get("skip_updates"):
            break
        # If the current channel is disabled, we should only bail on it
        if not updateConfig.get("enabled", True):
            continue
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
            patcherConfig=updateConfig['patcherConfig'],
            verifyConfigs=updateConfig['verifyConfigs'],
            appName=releaseConfig['appName'],
            productName=releaseConfig['productName'],
            version=releaseConfig['version'],
            appVersion=releaseConfig['appVersion'],
            baseTag=releaseConfig['baseTag'],
            buildNumber=releaseConfig['buildNumber'],
            partialUpdates=updateConfig.get('partialUpdates', {}),
            ftpServer=releaseConfig['ftpServer'],
            bouncerServer=releaseConfig['bouncerServer'],
            hgSshKey=releaseConfig['hgSshKey'],
            hgUsername=releaseConfig['hgUsername'],
            releaseChannel=channel,
            localTestChannel=updateConfig["localTestChannel"],
            clobberURL=clobberer_url,
            clobberBranch='release-%s' % sourceRepoInfo['name'],
            releaseNotesUrl=releaseConfig['releaseNotesUrl'],
            signingServers=getSigningServers('linux'),
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
            mar_channel_ids=updateConfig.get("marChannelIds", []),
        )

        builderName = builderPrefix("%s_%s_updates" % (releaseConfig["productName"], channel))

        builders.append({
            'name': builderName,
            'slavenames': branchConfig['platforms']['linux']['slaves'],
            'category': builderPrefix(''),
            'builddir': builderName,
            'slavebuilddir': normalizeName(builderName, releaseConfig['productName']),
            'factory': updates_factory,
            'env': builder_env,
            'properties': {
                'slavebuilddir': normalizeName(builderName, releaseConfig['productName']),
                'platform': platform,
                'branch': 'release-%s' % sourceRepoInfo['name'],
                'release_config': releaseConfigFile,
                'script_repo_revision': releaseTag,
                'event_group': 'update',
                "update_channel": updateConfig["localTestChannel"],
            }
        })
        if channel == releaseChannel:
            post_signing_builders.append(builderName)
        else:
            extra_updates_builders.append(builderName)

        if not releaseConfig.get('enablePartialMarsAtBuildTime', True):
            deliverables_builders.append(builderName)

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
                "-C", channel,
            ],
        )
        update_shipping_factory = ScriptFactory(**update_shipping_factory_args)

        usBuilderName = builderPrefix("update_shipping_%s" % channel)
        builders.append({
            'name': usBuilderName,
            'slavenames': unix_slaves,
            'category': builderPrefix(''),
            'builddir': usBuilderName,
            'slavebuilddir': normalizeName(usBuilderName, releaseConfig['productName']),
            'factory': update_shipping_factory,
            'env': builder_env,
            'properties': {
                'slavebuilddir': normalizeName(usBuilderName, releaseConfig['productName']),
                'release_config': releaseConfigFile,
                'script_repo_revision': releaseTag,
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
                "update_channel": releaseConfig["releaseChannel"],
            },
        })

        for platform in sorted(updateConfig.get("verifyConfigs", {})):
            vpf = branchConfig['platforms'][platform]
            for n, builderName in updateVerifyBuilders(platform, channel).iteritems():
                uv_factory = ScriptFactory(
                    scriptRepo=tools_repo,
                    interpreter='bash',
                    scriptName='scripts/release/updates/chunked-verify.sh',
                    extra_args=[platform, 'updateChannels',
                                str(updateVerifyChunks), str(n),
                                channel],
                    log_eval_func=lambda c, s: regex_log_evaluator(
                        c, s, update_verify_error),
                    use_mock=use_mock(platform),
                    mock_target=vpf.get('mock_target'),
                    mock_packages=vpf.get('mock_packages'),
                    mock_copyin_files=vpf.get('mock_copyin_files'),
                    env=branchConfig['platforms'][platform]['env'],
                )

                builddir = builderPrefix('%s_%s_update_verify' % (platform, channel)) + \
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
                update_verify_builders[channel].append(builderName)

            if releaseConfig.get('ui_update_tests'):
                # Append Firefox UI update verify tests
                for n, builder_name in ui_update_verify_builders(platform, channel).iteritems():
                    ui_uv_factory = ScriptFactory(
                        interpreter='python',
                        scriptRepo=mozharness_repo,
                        scriptName='scripts/firefox_ui_updates.py',
                        extra_args=[
                            '--cfg', 'generic_releng_config.py',
                            '--cfg', 'generic_releng_%s.py' % platform,
                            '--firefox-ui-branch', sourceRepoInfo['name'],
                            '--update-verify-config', updateConfig['verifyConfigs'][platform],
                            '--tools-tag', runtimeTag,
                            '--total-chunks', str(ui_update_verify_chunks),
                            '--this-chunk', str(n),
                            "--build-number", releaseConfig['buildNumber'],
                        ],
                        relengapi_archiver_repo_path=relengapi_archiver_repo_path,
                        relengapi_archiver_release_tag=releaseTag,
                    )

                    builddir = '%(prefix)s_%(this_chunk)s' % {
                        'prefix': builderPrefix(postfix='%s_%s_update_tests' % (platform, channel)),
                        'this_chunk': str(n)
                    }

                    builders.append({
                        'name': builder_name,
                        'slavenames': branchConfig['platforms'][platform]['slaves'],
                        'category': builderPrefix(''),
                        'builddir': builddir,
                        'slavebuilddir': normalizeName(builddir, releaseConfig['productName']),
                        'factory': ui_uv_factory,
                        'properties': {
                            'builddir': builddir,
                            'slavebuilddir': normalizeName(builddir, releaseConfig['productName']),
                            'script_repo_revision': releaseTag,
                            'release_tag': releaseTag,
                            'release_config': releaseConfigFile,
                            'platform': platform,
                            'branch': 'release-%s' % sourceRepoInfo['name'],
                            'chunkTotal': int(ui_update_verify_chunks),
                            'chunkNum': int(n),
                            'event_group': 'update_verify',
                        },
                    })
                    ui_update_tests_builders[channel].append(builder_name)

    if not releaseConfig.get("updateChannels") or \
      hasPlatformSubstring(releaseConfig['enUSPlatforms'], 'android'):
        builders.append(makeDummyBuilder(
            name=builderPrefix('%s_%s_updates' % (releaseConfig['productName'], releaseChannel)),
            slaves=all_slaves,
            category=builderPrefix(''),
            properties={
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
                'event_group': 'update',
            },
            env=dummy_builder_env,
        ))
        post_signing_builders.append(builderPrefix('%s_%s_updates' % (releaseConfig['productName'], releaseChannel)))


    if not releaseConfig.get('disableVirusCheck'):
        antivirus_factory = ScriptFactory(
            scriptRepo=mozharness_repo,
            interpreter="python2.7",
            scriptName='scripts/release/antivirus.py',
            extra_args=[
                "--product", releaseConfig["stage_product"],
                "--version", releaseConfig["version"],
                "--build-number", releaseConfig["buildNumber"],
                "--bucket-name", releaseConfig["S3Bucket"],
                "--tools-revision", releaseTag,
                "--tools-repo", tools_repo,
            ],
            script_timeout=3*60*60,
            relengapi_archiver_repo_path=relengapi_archiver_repo_path,
            relengapi_archiver_release_tag=releaseTag,
        )

        builders.append({
            'name': builderPrefix('%s_antivirus' % releaseConfig['productName']),
            'slavenames': av_slaves,
            'category': builderPrefix(''),
            'builddir': builderPrefix('%s_antivirus' % releaseConfig['productName']),
            'slavebuilddir': normalizeName(builderPrefix('av'), releaseConfig['productName']),
            'factory': antivirus_factory,
            'env': builder_env,
            'properties': {'slavebuilddir': normalizeName(builderPrefix('av'), releaseConfig['productName']),
                           'script_repo_revision': releaseTag,
                           'release_config': releaseConfigFile,
                           'platform': None,
                           'branch': 'release-%s' % sourceRepoInfo['name'],
                           }
        })
        post_deliverables_builders.append(builderPrefix('%s_antivirus' % releaseConfig['productName']))

    push_to_mirrors_factory = ScriptFactory(
        scriptRepo=mozharness_repo,
        interpreter="python2.7",
        scriptName='scripts/release/push-candidate-to-releases.py',
        extra_args=[
                "--product", releaseConfig["stage_product"],
                "--version", releaseConfig["version"],
                "--build-number", releaseConfig["buildNumber"],
                "--bucket", releaseConfig["S3Bucket"],
                "--credentials", releaseConfig["S3Credentials"],
        ],
        relengapi_archiver_repo_path=relengapi_archiver_repo_path,
        relengapi_archiver_release_tag=releaseTag,
    )

    builders.append({
        'name': builderPrefix('%s_push_to_mirrors' % releaseConfig['productName']),
        'slavenames': unix_slaves,
        'category': builderPrefix(''),
        'builddir': builderPrefix('%s_push_to_mirrors' % releaseConfig['productName']),
        'slavebuilddir': normalizeName(builderPrefix('psh_mrrrs'), releaseConfig['productName']),
        'factory': push_to_mirrors_factory,
        'env': builder_env,
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
        scriptName='scripts/release/post-release.sh',
    )
    postrelease_factory = ScriptFactory(**postrelease_factory_args)

    builders.append({
        'name': builderPrefix('%s_postrelease' % releaseConfig['productName']),
        'slavenames': unix_slaves,
        'category': builderPrefix(''),
        'builddir': builderPrefix('%s_postrelease' % releaseConfig['productName']),
        'slavebuilddir': normalizeName(builderPrefix('%s_postrelease' % releaseConfig['productName']), releaseConfig['productName']),
        'factory': postrelease_factory,
        'env': builder_env,
        'properties': {
            'slavebuilddir': normalizeName(builderPrefix('%s_postrelease' % releaseConfig['productName']), releaseConfig['productName']),
            'release_config': releaseConfigFile,
            'script_repo_revision': releaseTag,
            'platform': None,
            'branch': 'release-%s' % sourceRepoInfo['name'],
            'event_group': 'postrelease',
        },
    })

    for channel, updateConfig in updateChannels.iteritems():
        if not releaseConfig.get('disableBouncerEntries'):
            schedulerNames = []
            if updateConfig.get('verifyConfigs'):
                schedulerNames.append(builderPrefix('%s_ready-for-%s' % (channel, updateConfig["cdnTestChannel"])))
            if schedulerNames:
                trigger_uptake_factory = BuildFactory()
                trigger_uptake_factory.addStep(Trigger(
                    schedulerNames=schedulerNames,
                    set_properties={
                        'release_config': releaseConfigFile,
                        'script_repo_revision': releaseTag,
                    },
                ))
            else:
                trigger_uptake_factory = DummyFactory(0, None)

            builderName = builderPrefix("%s_%s_start_uptake_monitoring" % (releaseConfig["productName"], channel))
            builders.append({
                'name': builderName,
                'slavenames': all_slaves,
                'category': builderPrefix(''),
                'builddir': builderName,
                'slavebuilddir': normalizeName(builderName),
                'factory': trigger_uptake_factory,
                'env': builder_env,
                'properties': {
                    'slavebuilddir': normalizeName(builderName),
                    'release_config': releaseConfigFile,
                    'script_repo_revision': releaseTag,
                    'platform': None,
                    'branch': 'release-%s' % sourceRepoInfo['name'],
                },
            })

        if updateConfig.get("verifyConfigs"):
            final_verification_factory = ReleaseFinalVerification(
                hgHost=branchConfig['hghost'],
                buildToolsRepoPath=tools_repo_path,
                verifyConfigs=updateConfig['verifyConfigs'],
                clobberURL=clobberer_url,
                clobberBranch='release-%s' % sourceRepoInfo['name'],
                repoPath=sourceRepoInfo['path'],
            )

            builderName = builderPrefix("%s_final_verification" % channel)
            builders.append({
                'name': builderName,
                'slavenames': branchConfig['platforms']['linux']['slaves'] +
                branchConfig['platforms']['linux64']['slaves'],
                'category': builderPrefix(''),
                'builddir': builderName,
                'slavebuilddir': normalizeName(builderName),
                'factory': final_verification_factory,
                'env': builder_env,
                'properties': {
                    'slavebuilddir': normalizeName(builderName),
                    'platform': None,
                    'branch': 'release-%s' % sourceRepoInfo['name'],
                },
            })

        if not releaseConfig.get('disableBouncerEntries'):
            builderName = builderPrefix("%s_%s_ready_for_%s_testing" % (releaseConfig["productName"], channel, updateConfig["cdnTestChannel"]))
            builders.append(makeDummyBuilder(
                name=builderName,
                slaves=all_slaves,
                category=builderPrefix(''),
                properties={
                    'platform': None,
                    'branch': 'release-%s' % sourceRepoInfo['name'],
                    'event_group': 'releasetest',
                    "update_channel": updateConfig["cdnTestChannel"],
                },
                env=dummy_builder_env,
            ))

        builderName = builderPrefix("%s_%s_ready_for_release" % (releaseConfig["productName"], channel))
        builders.append(makeDummyBuilder(
            name=builderName,
            slaves=all_slaves,
            category=builderPrefix(''),
            properties={
                'platform': None,
                'branch': 'release-%s' % sourceRepoInfo['name'],
                'event_group': 'release',
            },
            env=dummy_builder_env,
        ))

    if not releaseConfig.get('disableBouncerEntries'):
        extra_args = ["-c", releaseConfig["bouncer_submitter_config"],
                      "--revision", releaseTag,
                      "--repo", sourceRepoInfo['path'],
                      "--version", releaseConfig['version'],
                      "--credentials-file", "oauth.txt",
                      "--bouncer-api-prefix", releaseConfig['tuxedoServerUrl'],
                      "--build-number", releaseConfig['buildNumber'],
                      ]
        for partial, info in releaseConfig.get('partialUpdates', {}).iteritems():
            prev_version = "%sbuild%s" % (partial, info["buildNumber"])
            extra_args.extend(["--previous-version", prev_version])

        bouncer_submitter_factory = ScriptFactory(
            scriptRepo=mozharness_repo,
            scriptName="scripts/bouncer_submitter.py",
            extra_args=extra_args,
            use_credentials_file=True,
            relengapi_archiver_repo_path=relengapi_archiver_repo_path,
            relengapi_archiver_release_tag=releaseTag,
        )

        builders.append({
            'name': builderPrefix('%s_bouncer_submitter' % releaseConfig['productName']),
            'slavenames': branchConfig['platforms']['linux']['slaves'] +
            branchConfig['platforms']['linux64']['slaves'],
            'category': builderPrefix(''),
            'builddir': builderPrefix('%s_bouncer_submitter' % releaseConfig['productName']),
            'slavebuilddir': normalizeName(builderPrefix('bncr_sub'), releaseConfig['productName']),
            'factory': bouncer_submitter_factory,
            'env': builder_env,
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
        fileIsImportant=lambda c: changeContainsProduct(c, releaseConfig['productName']) \
            and changeContainsScriptRepoRevision(c, releaseTag),
    )
    schedulers.append(reset_schedulers_scheduler)
    tag_source_scheduler = Dependent(
        name=builderPrefix('%s_tag_source' % releaseConfig['productName']),
        upstream=reset_schedulers_scheduler,
        builderNames=[builderPrefix(
            '%s_tag_source' % releaseConfig['productName'])],
    )

    tag_l10n_scheduler = Dependent(
        name=builderPrefix('%s_tag_l10n' % releaseConfig['productName']),
        upstream=reset_schedulers_scheduler,
        builderNames=[builderPrefix(
            '%s_tag_l10n' % releaseConfig['productName'])],
    )
    schedulers.append(tag_source_scheduler)
    schedulers.append(tag_l10n_scheduler)

    tag_source_downstream = [builderPrefix('%s_source' % releaseConfig[
                                           'productName'])]

    if not releaseConfig.get('disableBouncerEntries'):
        tag_source_downstream.append(builderPrefix(
            '%s_bouncer_submitter' % releaseConfig['productName']))

    for platform in releaseConfig['enUSPlatforms']:
        tag_source_downstream.append(builderPrefix('%s_build' % platform))
        if platform in releaseConfig['l10nPlatforms']:
            l10nBuilderNames = l10nBuilders(platform).values()
            repack_upstream = [
                builderPrefix('%s_build' % platform),
                builderPrefix('%s_tag_l10n' % releaseConfig['productName']),
            ]

            repack_scheduler = AggregatingScheduler(
                 name=builderPrefix('%s_repack' % platform),
                 branch=sourceRepoInfo['path'],
                 upstreamBuilders=repack_upstream,
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

    DependentID = makePropertiesScheduler(
        Dependent, [buildIDSchedFunc, buildUIDSchedFunc])

    schedulers.append(
        DependentID(
            name=builderPrefix('%s_build' % releaseConfig['productName']),
            upstream=tag_source_scheduler,
            builderNames=tag_source_downstream,
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

    for channel, updateConfig in updateChannels.iteritems():
        if not releaseConfig.get('disableBouncerEntries'):
            readyForReleaseUpstreams = [builderPrefix("%s_antivirus" % releaseConfig["productName"])]
            if updateConfig.get("requiresMirrors", True):
                appendBuildNumber = False
                checkInstallers = True
            else:
                appendBuildNumber = True
                checkInstallers = False
            if updateConfig.get('verifyConfigs'):
                readyForReleaseUpstreams += update_verify_builders[channel]
                finalVerifyBuilders = [builderPrefix('%s_final_verification' % channel)]
                readyForReleaseUpstreams += finalVerifyBuilders

                mirror_scheduler = TriggerBouncerCheck(
                    name=builderPrefix('%s_ready-for-%s' % (channel, updateConfig["cdnTestChannel"])),
                    configRepo=config_repo,
                    minUptake=releaseConfig.get('releasetestUptake', 10000),
                    builderNames=[builderPrefix(
                        '%s_%s_ready_for_%s_testing' % (releaseConfig['productName'], channel, updateConfig["cdnTestChannel"]))] + finalVerifyBuilders,
                    username=BuildSlaves.tuxedoUsername,
                    password=BuildSlaves.tuxedoPassword,
                    appendBuildNumber=appendBuildNumber,
                    checkInstallers=checkInstallers)

                schedulers.append(mirror_scheduler)

            schedulers.append(AggregatingScheduler(
                name=builderPrefix('%s_ready-for-release_%s' % (releaseConfig['productName'], channel)),
                branch=sourceRepoInfo['path'],
                upstreamBuilders=readyForReleaseUpstreams,
                builderNames=[builderPrefix('%s_%s_ready_for_release' % (releaseConfig['productName'], channel))],
            ))

    schedulers.append(AggregatingScheduler(
        name=builderPrefix(
            '%s_signing_done' % releaseConfig['productName']),
        branch=sourceRepoInfo['path'],
        upstreamBuilders=updates_upstream_builders,
        builderNames=post_signing_builders,
    ))

    push_to_mirrors_upstreams = []
    for channel, updateConfig in updateChannels.iteritems():
        push_to_mirrors_upstreams.append(builderPrefix("%s_%s_updates" % (releaseConfig["productName"], channel)))
        if updateConfig.get('verifyConfigs'):
            builderNames=update_verify_builders[channel]
            # run any extra updates jobs once the releaseChannel equivalent has run
            if channel == releaseChannel:
                builderNames.extend(extra_updates_builders)
            schedulers.append(AggregatingScheduler(
                name=builderPrefix('%s_updates_done' % channel),
                branch=sourceRepoInfo['path'],
                upstreamBuilders=[builderPrefix('%s_%s_updates' % (releaseConfig['productName'], channel))],
                builderNames=builderNames,
            ))
            # Add Firefox UI update test builders
            schedulers.append(AggregatingScheduler(
                name=builderPrefix('%s_ui_update_verify' % channel),
                branch=sourceRepoInfo['path'],
                upstreamBuilders=[builderPrefix('%s_%s_updates' % (releaseConfig['productName'], channel))],
                builderNames=ui_update_tests_builders[channel],
            ))

    if releaseConfig.get('enableAutomaticPushToMirrors'):
        push_to_mirrors_upstreams.extend([
            builderPrefix("%s_checksums" % releaseConfig["productName"]),
        ])
        schedulers.append(AggregatingScheduler(
            name=builderPrefix("%s_push_to_mirrors" % releaseConfig["productName"]),
            branch=sourceRepoInfo["path"],
            upstreamBuilders=push_to_mirrors_upstreams,
            builderNames=[builderPrefix("%s_push_to_mirrors" % releaseConfig["productName"])],
        ))

    if post_deliverables_builders:
        schedulers.append(AggregatingScheduler(
            name=builderPrefix(
                '%s_deliverables_ready' % releaseConfig['productName']),
            branch=sourceRepoInfo['path'],
            upstreamBuilders=deliverables_builders,
            builderNames=post_deliverables_builders,
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
    for channel, updateConfig in updateChannels.iteritems():
        if updateConfig.get("requiresMirrors", True):
            upstream_builders = [builderPrefix('%s_push_to_mirrors' % releaseConfig['productName'])]
        else:
            upstream_builders = []
        if updateConfig.get('verifyConfigs'):
            upstream_builders.append(builderPrefix('%s_%s_updates' % (releaseConfig['productName'], channel)))
        if not releaseConfig.get('disableBouncerEntries'):
            schedulers.append(AggregatingScheduler(
                name=builderPrefix(
                    '%s_%s_uptake_check' % (releaseConfig['productName'], channel)),
                branch=sourceRepoInfo['path'],
                upstreamBuilders=upstream_builders,
                builderNames=[builderPrefix('%s_%s_start_uptake_monitoring' % (releaseConfig['productName'], channel))]
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
            env=dummy_builder_env,
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
            sendToInterestedUsers=False,
            extraRecipients=[recipient],
            extraHeaders={'Message-Id': email_message_id},
            branches=[sourceRepoInfo['path']],
            messageFormatter=createReleaseChangeMessage,
            changeIsImportant=lambda c:
            changeContainsProduct(c, releaseConfig['productName']) and
            changeContainsScriptRepoRevision(c, releaseTag)
        ))
    for recipient in releaseConfig['ImportantRecipients']:
        if hasPlatformSubstring(releaseConfig['enUSPlatforms'], 'android'):
            # send a message when android signing is complete
            status.append(ChangeNotifier(
                fromaddr="release@mozilla.com",
                sendToInterestedUsers=False,
                extraRecipients=[recipient],
                extraHeaders={'In-Reply-To': email_message_id,
                              'References': email_message_id},
                branches=[builderPrefix('android_post_signing')],
                messageFormatter=createReleaseChangeMessage,
                changeIsImportant=lambda c:
                changeContainsProperties(c, dict(who=enUS_signed_apk_url))
            ))

    non_ui_update_verify_builders = [b["name"] for b in builders[:] if "ui_update_verify" not in b["name"]]
    non_ui_update_verify_builders.extend([b["name"] for b in test_builders])
    for b in ui_update_tests_builders.values():
        if b in non_ui_update_verify_builders:
            non_ui_update_verify_builders.remove(b)

    # send all release messages
    status.append(MailNotifier(
        fromaddr='release@mozilla.com',
        sendToInterestedUsers=False,
        extraRecipients=releaseConfig['AllRecipients'],
        extraHeaders={'In-Reply-To': email_message_id,
                      'References': email_message_id,
                      'Reply-To': ",".join(releaseConfig['AllRecipients'])},
        mode='all',
        builders=non_ui_update_verify_builders,
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
            builders=[builderPrefix('%s_%s_updates' % (releaseConfig['productName'], releaseChannel))],
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

    # Make sure builders have the right properties
    addBuilderProperties(builders)

    return {
        "builders": builders,
        "status": status,
        "change_source": change_source,
        "schedulers": schedulers,
    }


def generateReleasePromotionBuilders(branch_config, branch_name, product,
                                     secrets):
    builders = []
    category_name = "release-%s" % branch_name
    tools_repo_path = branch_config.get('build_tools_repo_path')
    tools_repo = '%s%s' % (branch_config['hgurl'], tools_repo_path)

    for platform in branch_config["l10n_release_platforms"]:
        pf = branch_config["platforms"][platform]
        l10n_buildername = "release-{branch}_{product}_{platform}_l10n_repack".format(
            branch=branch_name,
            product=product,
            platform=platform,
        )

        env_config = "single_locale/production.py"
        balrog_config = "balrog/production.py"
        if branch_config.get("staging"):
            env_config = "single_locale/staging.py"
            balrog_config = "balrog/staging.py"

        mh_cfg = {
            "script_name": "scripts/desktop_l10n.py",
            "extra_args": [
                "--branch-config", "single_locale/%s.py" % branch_config.get('single_locale_branch_config',
                                                                             branch_name),
                "--platform-config", "single_locale/%s.py" % platform,
                "--environment-config", env_config,
                "--balrog-config", balrog_config,
                                   ],
            "script_timeout": 1800,
            "script_maxtime": 7200,
        }

        l10n_factory = makeMHFactory(branch_config, pf,
                                     mh_cfg=mh_cfg,
                                     signingServers=secrets.get(pf.get("dep_signing_servers")),
                                     use_credentials_file=True,
                                     )
        l10n_builder = {
            "name": l10n_buildername,
            "factory": l10n_factory,
            "builddir": l10n_buildername,
            "slavebuilddir": normalizeName(l10n_buildername),
            "slavenames": pf["slaves"],
            "category": category_name,
            "properties": {
                "branch": branch_name,
                "platform": "l10n",
                "product": product,
            },
        }
        builders.append(l10n_builder)

    bouncer_mh_cfg = {
        "script_name": "scripts/bouncer_submitter.py",
        "extra_args": [
             "-c",  branch_config['bouncer_submitter_config'][product],
             "--credentials-file", "oauth.txt",
             "--bouncer-api-prefix", branch_config['tuxedoServerUrl'],
             "--repo", branch_config['repo_path'],
        ]
    }

    bouncer_buildername = "release-{branch}_{product}_bncr_sub".format(
        branch=branch_name, product=product)
    # Explicitly define pf using the slave platform (linux64 in this case)
    bouncer_submitter_factory = makeMHFactory(
        config=branch_config, pf=branch_config["platforms"]['linux64'],
        mh_cfg=bouncer_mh_cfg, use_credentials_file=True)

    bouncer_builder = {
        "name": bouncer_buildername,
        "slavenames": branch_config["platforms"]["linux64"]["slaves"],
        "builddir": bouncer_buildername,
        "slavebuilddir": normalizeName(bouncer_buildername),
        "factory": bouncer_submitter_factory,
        "category": category_name,
        "properties": {
            "branch": branch_name,
            "platform": None,
            "product": product,
        }
    }
    builders.append(bouncer_builder)

    uv_fmt_template = "release-{branch}_{product}_{platform}_update_verify"
    for platform in branch_config.get("release_platforms"):
        pf = branch_config["platforms"][platform]
        uv_buildername = uv_fmt_template.format(
            branch=branch_name,
            platform=platform,
            product=product,
            )
        uv_factory = ScriptFactory(
            scriptRepo=tools_repo,
            interpreter='bash',
            scriptName='scripts/release/updates/chunked-verify.sh',
            env=pf['env'],
        )

        uv_builder = {
            'name': uv_buildername,
            'slavenames': pf['slaves'],
            'builddir': uv_buildername,
            'slavebuilddir': normalizeName(uv_buildername),
            'factory': uv_factory,
            'category': category_name,
            'env': pf['env'],
            'properties': {
                    "branch": branch_name,
                    "platform": platform,
                    "product": product,
                }
        }
        builders.append(uv_builder)

    updates_mh_cfg = {
        "script_name": "scripts/release/updates.py",
        "extra_args": [
             "-c",  branch_config['updates_config'][product],
        ]
    }
    updates_buildername = "release-{branch}-{product}_updates".format(
        branch=branch_name, product=product)
    # Explicitly define pf using the slave platform (linux64 in this case)
    updates_factory = makeMHFactory(
        config=branch_config, pf=branch_config["platforms"]['linux64'],
        mh_cfg=updates_mh_cfg, use_credentials_file=True)

    updates_builder = {
        "name": updates_buildername,
        "slavenames": branch_config["platforms"]["linux64"]["slaves"],
        "builddir": updates_buildername,
        "slavebuilddir": normalizeName(updates_buildername),
        "factory": updates_factory,
        "category": category_name,
        "properties": {
            "branch": branch_name,
            "platform": None,
            "product": product,
        }
    }
    builders.append(updates_builder)

    version_bump_mh_cfg = {
        "script_name": "scripts/release/postrelease_version_bump.py",
        "extra_args": [
             "-c",  branch_config['postrelease_version_bump_config'][product],
        ]
    }
    version_bump_buildername = "release-{branch}-{product}_version_bump".format(
        branch=branch_name, product=product)
    # Explicitly define pf using the slave platform (linux64 in this case)
    version_bump_submitter_factory = makeMHFactory(
        config=branch_config, pf=branch_config["platforms"]['linux64'],
        mh_cfg=version_bump_mh_cfg, use_credentials_file=True)

    version_bump_builder = {
        "name": version_bump_buildername,
        "slavenames": branch_config["platforms"]["linux64"]["slaves"],
        "builddir": version_bump_buildername,
        "slavebuilddir": normalizeName(version_bump_buildername),
        "factory": version_bump_submitter_factory,
        "category": category_name,
        "properties": {
            "branch": branch_name,
            "platform": None,
            "product": product,
        }
    }
    builders.append(version_bump_builder)

    # uptake monitoring
    uptake_monitoring_mh_cfg = {
        "script_name": "scripts/release/uptake_monitoring.py",
        "extra_args": [
             "-c",  branch_config['uptake_monitoring_config'][product],
        ]
    }
    uptake_monitoring_buildername = "release-{branch}-{product}_uptake_monitoring".format(
        branch=branch_name, product=product)
    # Explicitly define pf using the slave platform (linux64 in this case)
    uptake_monitoring_submitter_factory = makeMHFactory(
        config=branch_config, pf=branch_config["platforms"]['linux64'],
        mh_cfg=uptake_monitoring_mh_cfg, use_credentials_file=True)

    uptake_monitoring_builder = {
        "name": uptake_monitoring_buildername,
        "slavenames": branch_config["platforms"]["linux64"]["slaves"],
        "builddir": uptake_monitoring_buildername,
        "slavebuilddir": normalizeName(uptake_monitoring_buildername),
        "factory": uptake_monitoring_submitter_factory,
        "category": category_name,
        "properties": {
            "branch": branch_name,
            "platform": None,
            "product": product,
        }
    }
    builders.append(uptake_monitoring_builder)

    # bouncer aliases
    bouncer_aliases_mh_cfg = {
        "script_name": "scripts/release/postrelease_bouncer_aliases.py",
        "extra_args": [
             "-c",  branch_config['postrelease_bouncer_aliases_config'][product],
        ]
    }
    bouncer_aliases_buildername = "release-{branch}-{product}_bouncer_aliases".format(
        branch=branch_name, product=product)
    # Explicitly define pf using the slave platform (linux64 in this case)
    bouncer_aliases_submitter_factory = makeMHFactory(
        config=branch_config, pf=branch_config["platforms"]['linux64'],
        mh_cfg=bouncer_aliases_mh_cfg, use_credentials_file=True)

    bouncer_aliases_builder = {
        "name": bouncer_aliases_buildername,
        "slavenames": branch_config["platforms"]["linux64"]["slaves"],
        "builddir": bouncer_aliases_buildername,
        "slavebuilddir": normalizeName(bouncer_aliases_buildername),
        "factory": bouncer_aliases_submitter_factory,
        "category": category_name,
        "properties": {
            "branch": branch_name,
            "platform": None,
            "product": product,
        }
    }
    builders.append(bouncer_aliases_builder)

    # checksums
    checksums_buildername = "release-{branch}-{product}_chcksms".format(
        branch=branch_name, product=product)
    extra_extra_args = []
    if product == 'fennec':
        extra_extra_args = ['--add-action=copy-info-files']
    checksums_mh_cfg = {
        'script_name': 'scripts/release/generate-checksums.py',
        'extra_args': [
            "--stage-product", branch_config["stage_product"][product],
            "--bucket-name-full", branch_config["beetmover_buckets"][product],
            "--credentials", branch_config["beetmover_credentials"],
            "--tools-repo", branch_config["platforms"]["linux64"]["tools_repo_cache"],
        ] + extra_extra_args
    }
    checksums_factory = makeMHFactory(
        config=branch_config, pf=branch_config['platforms']['linux64'],
        mh_cfg=checksums_mh_cfg,
        signingServers=secrets.get("release-signing"),
    )
    checksums_builder = {
        'name': checksums_buildername,
        'slavenames': branch_config['platforms']['linux']['slaves'] +
                      branch_config['platforms']['linux64']['slaves'],
        'category': category_name,
        'builddir': checksums_buildername,
        'slavebuilddir': normalizeName(checksums_buildername),
        'factory': checksums_factory,
        'properties': {
            'branch': branch_name,
            'platform': None,
            'product': product,
        }
    }
    builders.append(checksums_builder)

    for platform in branch_config.get("partner_repacks_platforms", []):
        buildername = "release-{branch}-{product}-{platform}_partner_repacks"
        buildername = buildername.format(branch=branch_name, product=product,
                                         platform=platform)
        cfg = branch_config['partner_repack_config'][product]
        mh_cfg = {
            "script_name": cfg['script_name'],
            "script_maxtime": 6*3600,
            "extra_args": cfg['extra_args'] + ["--platform", platform,
                                               "--hgrepo", branch_config['repo_path']]
        }
        partner_repack_factory = makeMHFactory(
            signingServers=secrets.get(pf.get("dep_signing_servers")),
            config=branch_config,
            pf=branch_config["platforms"]["macosx64"],
            mh_cfg=mh_cfg)

        builders.append({
            'name': buildername,
            'slavenames': branch_config['platforms']['macosx64']['slaves'],
            'category': category_name,
            'builddir': buildername,
            'slavebuilddir': normalizeName(buildername),
            'factory': partner_repack_factory,
            'properties': {
                'branch': branch_name,
                'platform': 'macosx64',
                'product': product,
            }
        })

    publish_balrog_mh_cfg = {
        "script_name": "scripts/release/publish_balrog.py",
        "extra_args": [
             "-c",  branch_config['updates_config'][product],
        ]
    }
    publish_balrog_buildername = "release-{branch}-{product}_publish_balrog".format(
        branch=branch_name, product=product)
    # Explicitly define pf using the slave platform (linux64 in this case)
    publish_balrog_submitter_factory = makeMHFactory(
        config=branch_config, pf=branch_config["platforms"]['linux64'],
        mh_cfg=publish_balrog_mh_cfg, use_credentials_file=True)

    publish_balrog_builder = {
        "name": publish_balrog_buildername,
        "slavenames": branch_config["platforms"]["linux64"]["slaves"],
        "builddir": publish_balrog_buildername,
        "slavebuilddir": normalizeName(publish_balrog_buildername),
        "factory": publish_balrog_submitter_factory,
        "category": category_name,
        "properties": {
            "branch": branch_name,
            "platform": None,
            "product": product,
        }
    }
    builders.append(publish_balrog_builder)

    # Don't merge release builder requests
    nomergeBuilders.update([b['name'] for b in builders])

    # Make sure builders have the right properties
    addBuilderProperties(builders)

    return builders
