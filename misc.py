from urlparse import urljoin

from buildbot.scheduler import Nightly
from buildbot.status.tinderbox import TinderboxMailNotifier
from buildbot.steps.shell import WithProperties

import buildbotcustom.changes.hgpoller
import buildbotcustom.process.factory
import buildbotcustom.log
import buildbotcustom.l10n
import buildbotcustom.scheduler
reload(buildbotcustom.changes.hgpoller)
reload(buildbotcustom.process.factory)
reload(buildbotcustom.log)
reload(buildbotcustom.l10n)
reload(buildbotcustom.scheduler)

from buildbotcustom.changes.hgpoller import HgPoller, HgAllLocalesPoller
from buildbotcustom.process.factory import NightlyBuildFactory, \
  NightlyRepackFactory, UnittestBuildFactory, CodeCoverageFactory, \
  UnittestPackagedBuildFactory
from buildbotcustom.scheduler import MozScheduler, NoMergeScheduler, \
  NightlyRebuild
from buildbotcustom.l10n import NightlyL10n

# This file contains misc. helper function that don't make sense to put in
# other files. For example, functions that are called in a master.cfg

def get_l10n_repositories(file, l10nRepoPath, relbranch):
    """Reads in a list of locale names and revisions for their associated
       repository from 'file'.
    """
    if not l10nRepoPath.endswith('/'):
        l10nRepoPath = l10nRepoPath + '/'
    repositories = {}
    for localeLine in open(file).readlines():
        locale, revision = localeLine.rstrip().split()
        if revision == 'FIXME':
            raise Exception('Found FIXME in %s for locale "%s"' % \
                           (file, locale))
        locale = urljoin(l10nRepoPath, locale)
        repositories[locale] = {
            'revision': revision,
            'relbranchOverride': relbranch,
            'bumpFiles': []
        }

    return repositories


# This function is used as fileIsImportant parameter for Buildbots that do both
# dep/nightlies and release builds. Because they build the same "branch" this
# allows us to have the release builder ignore HgPoller triggered changse
# and the dep builders only obey HgPoller/Force Build triggered ones.

def isHgPollerTriggered(change, hgUrl):
    if change.comments.find(hgUrl) > -1:
        return True
    return False


def generateBranchObjects(config, name):
    """name is the name of branch which is usually the last part of the path
       to the repository. For example, 'mozilla-central', 'tracemonkey', or
       'mozilla-1.9.1'.
       config is a dictionary containing all of the necessary configuration
       information for a branch. The required keys depends greatly on what's
       enabled for a branch (unittests, xulrunner, l10n, etc). The best way
       to figure out what you need to pass is by looking at existing configs
       and using 'buildbot checkconfig' to verify.
    """
    # We return this at the end
    branchObjects = {
        'builders': [],
        'change_source': [],
        'schedulers': [],
        'status': []
    }
    builders = []
    unittestBuilders = []
    triggeredUnittestBuilders = []
    nightlyBuilders = []
    xulrunnerNightlyBuilders = []
    weeklyBuilders = []
    # This dict provides a mapping between en-US nightly scheduler names
    # and l10n nightly scheduler names. It's filled out just below here.
    l10nNightlyBuilders = {}
    # generate a list of builders, nightly builders (names must be different)
    # for easy access
    for platform in config['platforms'].keys():
        base_name = config['platforms'][platform]['base_name']
        builders.append('%s build' % base_name)
        # Skip l10n, unit tests and nightlies for debug builds
        if platform.find('debug') >= 0:
            continue

        builder = '%s nightly' % base_name
        nightlyBuilders.append(builder)
        if config['enable_shark'] and platform in ('macosx'):
            nightlyBuilders.append('%s shark' % base_name) 
        if config['enable_l10n'] and platform in ('linux','win32','macosx'):
            l10nNightlyBuilders[builder] = {} 
            l10nNightlyBuilders[builder]['tree'] = config['l10n_tree']
            l10nNightlyBuilders[builder]['l10n_builder'] = \
                '%s %s %s l10n' % (config['product_name'].capitalize(),
                                   name, platform)
            l10nNightlyBuilders[builder]['platform'] = platform
        if config['enable_unittests'] and platform in ('linux','win32','macosx'):
            unittestBuilders.append('%s unit test' % base_name)
            test_builders = []
            for suites_name, suites in config['unittest_suites']:
                test_builders.append('%s test %s' % (config['platforms'][platform]['base_name'], suites_name))
            triggeredUnittestBuilders.append(('%s-%s-unittest' % (name, platform), test_builders))
        if config['enable_codecoverage'] and platform in ('linux',):
            weeklyBuilders.append('%s code coverage' % config['platforms'][platform]['base_name'])
        if config['enable_xulrunner'] and platform not in ('wince'):
            xulrunnerNightlyBuilders.append('%s xulrunner' % config['platforms'][platform]['base_name'])

    # Currently, each branch goes to a different tree
    # If this changes in the future this may have to be
    # moved out of the loop
    branchObjects['status'].append(TinderboxMailNotifier(
        fromaddr="mozilla2.buildbot@build.mozilla.org",
        tree=config['tinderbox_tree'],
        extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org"],
        relayhost="mail.build.mozilla.org",
        builders=builders + nightlyBuilders,
        logCompression="bzip2"
    ))
    # XULRunner builds
    branchObjects['status'].append(TinderboxMailNotifier(
        fromaddr="mozilla2.buildbot@build.mozilla.org",
        tree=config['xulrunner_tinderbox_tree'],
        extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org"],
        relayhost="mail.build.mozilla.org",
        builders=xulrunnerNightlyBuilders,
        logCompression="bzip2"
    ))
    # Separate notifier for unittests, since they need to be run through
    # the unittest errorparser
    branchObjects['status'].append(TinderboxMailNotifier(
        fromaddr="mozilla2.buildbot@build.mozilla.org",
        tree=config['tinderbox_tree'],
        extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org"],
        relayhost="mail.build.mozilla.org",
        builders=unittestBuilders,
        logCompression="bzip2",
        errorparser="unittest"
    ))
    # Weekly builds (currently only code coverage) go to a different tree
    branchObjects['status'].append(TinderboxMailNotifier(
        fromaddr="mozilla2.buildbot@build.mozilla.org",
        tree=config['weekly_tinderbox_tree'],
        extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org"],
        relayhost="mail.build.mozilla.org",
        builders=weeklyBuilders,
        logCompression="bzip2",
        errorparser="unittest"
    ))

    if config['enable_l10n']:
        l10n_builders = []
        for b in l10nNightlyBuilders:
            l10n_builders.append(l10nNightlyBuilders[b]['l10n_builder'])
            l10n_builders.append(l10nNightlyBuilders[b]['l10n_builder'] + " build")
        # This notifies all l10n related build objects to Mozilla-l10n
        branchObjects['status'].append(TinderboxMailNotifier(
            fromaddr="bootstrap@mozilla.com",
            tree=config['l10n_tinderbox_tree'],
            extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org"],
            relayhost="mail.build.mozilla.org",
            logCompression="bzip2",
            builders=l10n_builders,
            binaryURL="http://ftp.mozilla.org/pub/mozilla.org/firefox/nightly/latest-mozilla-central-l10n/"
        ))

        # We only want the builds from the specified builders
        # since their builds have a build property called "locale"
        branchObjects['status'].append(TinderboxMailNotifier(
            fromaddr="bootstrap@mozilla.com",
            tree=WithProperties(config['l10n_tinderbox_tree'] + "-%(locale)s"),
            extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org"],
            relayhost="mail.build.mozilla.org",
            logCompression="bzip2",
            builders=l10n_builders,
            binaryURL="http://ftp.mozilla.org/pub/mozilla.org/firefox/nightly/latest-mozilla-central-l10n/"
        ))

    # change sources
    branchObjects['change_source'].append(HgPoller(
        hgURL=config['hgurl'],
        branch=config['repo_path'],
        pushlogUrlOverride='%s/%s/pushlog' % (config['hgurl'],
                                              config['repo_path']),
        pollInterval=1*60
    ))
    
    if config['enable_l10n']:
        hg_all_locales_poller = HgAllLocalesPoller(hgURL = config['hgurl'],
                            repositoryIndex = config['l10n_repo_path'],
                            pollInterval = 15*60)
        hg_all_locales_poller.parallelRequests = 1
        branchObjects['change_source'].append(hg_all_locales_poller)

    # schedulers
    # this one gets triggered by the HG Poller
    branchObjects['schedulers'].append(MozScheduler(
        name=name,
        branch=config['repo_path'],
        treeStableTimer=3*60,
        idleTimeout=config.get('idle_timeout', None),
        builderNames=builders + unittestBuilders,
        fileIsImportant=lambda c: isHgPollerTriggered(c, config['hgurl'])
    ))

    for scheduler_branch, test_builders in triggeredUnittestBuilders:
        scheduler_name = scheduler_branch
        branchObjects['schedulers'].append(NoMergeScheduler(name=scheduler_name, branch=scheduler_branch, builderNames=test_builders, treeStableTimer=0))
        branchObjects['schedulers'].append(NightlyRebuild(name=scheduler_name+"-nightly",
            builderNames=test_builders,
            dayOfWeek=0, # Monday
            hour=3, minute=2, # at 3:02 am local time
            numberOfBuildsToTrigger=2,
            mergeBuilds=False))
        branchObjects['status'].append(TinderboxMailNotifier(
            fromaddr="mozilla2.buildbot@build.mozilla.org",
            tree=config['packaged_unittest_tinderbox_tree'],
            extraRecipients=["tinderbox-daemon@tinderbox.mozilla.org"],
            relayhost="mail.build.mozilla.org",
            builders=test_builders,
            logCompression="bzip2",
            errorparser="unittest"
        ))

    # Now, setup the nightly en-US schedulers and maybe,
    # their downstream l10n ones
    for builder in nightlyBuilders + xulrunnerNightlyBuilders:
        nightly_scheduler=Nightly(
            name=builder,
            branch=config['repo_path'],
            # bug 482123 - keep the minute to avoid problems with DST changes
            hour=[3], minute=[45],
            builderNames=[builder]
        )
        branchObjects['schedulers'].append(nightly_scheduler)

        if config['enable_l10n'] and builder in l10nNightlyBuilders:
            l10n_builder = l10nNightlyBuilders[builder]['l10n_builder']
            platform = l10nNightlyBuilders[builder]['platform']
            tree = l10nNightlyBuilders[builder]['tree']
            branchObjects['schedulers'].append(NightlyL10n(
                                   name=l10n_builder,
                                   platform=platform,
                                   tree=tree,
                                   hour=[7],
                                   builderNames=[l10n_builder],
                                   repoType='hg',
                                   branch=config['repo_path'],
                                   baseTag='default',
                                   localesFile=config['allLocalesFile']
                                  ))

    for builder in weeklyBuilders:
        weekly_scheduler=Nightly(
            name=builder,
            branch=config['repo_path'],
            dayOfWeek=5, # Saturday
            hour=[3], minute=[02],
            builderNames=[builder],
        )
        branchObjects['schedulers'].append(weekly_scheduler)

    for platform in sorted(config['platforms'].keys()):
        # shorthand
        pf = config['platforms'][platform]

        leakTest = False
        codesighs = True
        uploadPackages = True
        uploadSymbols = False
        talosMasters = config['talos_masters']
        if platform.find('-debug') > -1:
            leakTest = True
            codesighs = False
            uploadPackages = False
            talosMasters = None
        if platform.find('win') > -1 or platform.find('64') > -1:
            codesighs = False
        if 'upload_symbols' in pf and pf['upload_symbols']:
            uploadSymbols = True

        buildSpace = pf.get('build_space', config['default_build_space'])
        clobberTime = pf.get('clobber_time', config['default_clobber_time'])
        mochitestLeakThreshold = pf.get('mochitest_leak_threshold', None)
        crashtestLeakThreshold = pf.get('crashtest_leak_threshold', None)

        mozilla2_dep_factory = NightlyBuildFactory(
            env=pf['env'],
            objdir=pf['platform_objdir'],
            platform=platform,
            hgHost=config['hghost'],
            repoPath=config['repo_path'],
            buildToolsRepoPath=config['build_tools_repo_path'],
            configRepoPath=config['config_repo_path'],
            configSubDir=config['config_subdir'],
            profiledBuild=pf['profiled_build'],
            productName=config['product_name'],
            mozconfig=pf['mozconfig'],
            stageServer=config['stage_server'],
            stageUsername=config['stage_username'],
            stageGroup=config['stage_group'],
            stageSshKey=config['stage_ssh_key'],
            stageBasePath=config['stage_base_path'],
            graphServer=config['graph_server'],
            graphSelector=config['graph_selector'],
            graphBranch=config['tinderbox_tree'],
            baseName=pf['base_name'],
            leakTest=leakTest,
            codesighs=codesighs,
            uploadPackages=uploadPackages,
            uploadSymbols=False,
            buildSpace=buildSpace,
            clobberURL=config['base_clobber_url'],
            clobberTime=clobberTime,
            buildsBeforeReboot=pf['builds_before_reboot'],
            talosMasters=talosMasters,
            packageTests=False,
        )
        mozilla2_dep_builder = {
            'name': '%s build' % pf['base_name'],
            'slavenames': pf['slaves'],
            'builddir': '%s-%s' % (name, platform),
            'factory': mozilla2_dep_factory,
            'category': name,
        }
        branchObjects['builders'].append(mozilla2_dep_builder)

        # skip nightlies for debug builds
        if platform.find('debug') > -1:
             continue

        nightly_builder = '%s nightly' % pf['base_name']

        mozilla2_nightly_factory = NightlyBuildFactory(
            env=pf['env'],
            objdir=pf['platform_objdir'],
            platform=platform,
            hgHost=config['hghost'],
            repoPath=config['repo_path'],
            buildToolsRepoPath=config['build_tools_repo_path'],
            configRepoPath=config['config_repo_path'],
            configSubDir=config['config_subdir'],
            profiledBuild=pf['profiled_build'],
            productName=config['product_name'],
            mozconfig=pf['mozconfig'],
            stageServer=config['stage_server'],
            stageUsername=config['stage_username'],
            stageGroup=config['stage_group'],
            stageSshKey=config['stage_ssh_key'],
            stageBasePath=config['stage_base_path'],
            codesighs=False,
            uploadPackages=uploadPackages,
            uploadSymbols=uploadSymbols,
            nightly=True,
            createSnippet=config['create_snippet'],
            ausBaseUploadDir=config['aus2_base_upload_dir'],
            updatePlatform=pf['update_platform'],
            downloadBaseURL=config['download_base_url'],
            ausUser=config['aus2_user'],
            ausHost=config['aus2_host'],
            buildSpace=buildSpace,
            clobberURL=config['base_clobber_url'],
            clobberTime=clobberTime,
            buildsBeforeReboot=pf['builds_before_reboot'],
            talosMasters=talosMasters,
            packageTests=False,
        )

        mozilla2_nightly_builder = {
            'name': nightly_builder,
            'slavenames': pf['slaves'],
            'builddir': '%s-%s-nightly' % (name, platform),
            'factory': mozilla2_nightly_factory,
            'category': name,
        }
        branchObjects['builders'].append(mozilla2_nightly_builder)

        if config['enable_l10n']:
            if platform in ('linux','win32','macosx'):
                mozilla2_l10n_nightly_factory = NightlyRepackFactory(
                    hgHost=config['hghost'],
                    tree=config['l10n_tree'],
                    project=config['product_name'],
                    appName=config['app_name'],
                    enUSBinaryURL=config['enUS_binaryURL'],
                    nightly=True,
                    l10nNightlyUpdate=config['l10nNightlyUpdate'],
                    l10nDatedDirs=config['l10nDatedDirs'],
                    createSnippet=config['create_snippet'],
                    ausBaseUploadDir=config['aus2_base_upload_dir'],
                    updatePlatform=pf['update_platform'],
                    downloadBaseURL=config['download_base_url'],
                    ausUser=config['aus2_user'],
                    ausHost=config['aus2_host'],
                    stageServer=config['stage_server'],
                    stageUsername=config['stage_username'],
                    stageSshKey=config['stage_ssh_key'],
                    repoPath=config['repo_path'],
                    l10nRepoPath=config['l10n_repo_path'],
                    buildToolsRepoPath=config['build_tools_repo_path'],
                    compareLocalesRepoPath=config['compare_locales_repo_path'],
                    compareLocalesTag=config['compare_locales_tag'],
                    buildSpace=2,
                    clobberURL=config['base_clobber_url'],
                    clobberTime=clobberTime,
                )
                mozilla2_l10n_nightly_builder = {
                    'name': l10nNightlyBuilders[nightly_builder]['l10n_builder'],
                    'slavenames': config['l10n_slaves'][platform],
                    'builddir': '%s-%s-l10n-nightly' % (name, platform),
                    'factory': mozilla2_l10n_nightly_factory,
                    'category': name,
                }
                branchObjects['builders'].append(mozilla2_l10n_nightly_builder)
                mozilla2_l10n_dep_factory = NightlyRepackFactory(
                    hgHost=config['hghost'],
                    tree=config['l10n_tree'],
                    project=config['product_name'],
                    appName=config['app_name'],
                    enUSBinaryURL=config['enUS_binaryURL'],
                    nightly=False,
                    l10nDatedDirs=config['l10nDatedDirs'],
                    stageServer=config['stage_server'],
                    stageUsername=config['stage_username'],
                    stageSshKey=config['stage_ssh_key'],
                    repoPath=config['repo_path'],
                    l10nRepoPath=config['l10n_repo_path'],
                    buildToolsRepoPath=config['build_tools_repo_path'],
                    compareLocalesRepoPath=config['compare_locales_repo_path'],
                    compareLocalesTag=config['compare_locales_tag'],
                    buildSpace=2,
                    clobberURL=config['base_clobber_url'],
                    clobberTime=clobberTime,
                )
                mozilla2_l10n_dep_builder = {
                    'name': l10nNightlyBuilders[nightly_builder]['l10n_builder'] + " build",
                    'slavenames': config['l10n_slaves'][platform],
                    'builddir': '%s-%s-l10n-dep' % (name, platform),
                    'factory': mozilla2_l10n_dep_factory,
                    'category': name,
                }
                branchObjects['builders'].append(mozilla2_l10n_dep_builder)

        if config['enable_unittests']:
            if platform in ('linux','win32','macosx'):
                runA11y = True
                if platform == 'macosx':
                    runA11y = config['enable_mac_a11y']

                unittest_factory = UnittestBuildFactory(
                    platform=platform,
                    productName=config['product_name'],
                    config_repo_path=config['config_repo_path'],
                    config_dir=config['config_subdir'],
                    objdir=config['objdir_unittests'],
                    hgHost=config['hghost'],
                    repoPath=config['repo_path'],
                    buildToolsRepoPath=config['build_tools_repo_path'],
                    buildSpace=config['unittest_build_space'],
                    clobberURL=config['base_clobber_url'],
                    clobberTime=clobberTime,
                    buildsBeforeReboot=pf['builds_before_reboot'],
                    run_a11y=runA11y,
                    mochitest_leak_threshold=mochitestLeakThreshold,
                    crashtest_leak_threshold=crashtestLeakThreshold,
                    stageServer=config['stage_server'],
                    stageUsername=config['stage_username'],
                    stageSshKey=config['stage_ssh_key'],
                    unittestMasters=config['unittest_masters'],
                    uploadPackages=True,
                )
                unittest_builder = {
                    'name': '%s unit test' % pf['base_name'],
                    'slavenames': pf['slaves'],
                    'builddir': '%s-%s-unittest' % (name, platform),
                    'factory': unittest_factory,
                    'category': name,
                }
                branchObjects['builders'].append(unittest_builder)

                for suites_name, suites in config['unittest_suites']:
                    # If we're not supposed to run the a11y tests, then
                    # remove the mochitest-a11y suite
                    if not runA11y and 'mochitest-a11y' in suites:
                        suites = suites[:]
                        suites.remove('mochitest-a11y')
                    packaged_unittest_factory = UnittestPackagedBuildFactory(
                        platform=platform,
                        test_suites=suites,
                        mochitest_leak_threshold=mochitestLeakThreshold,
                        crashtest_leak_threshold=crashtestLeakThreshold,
                        hgHost=config['hghost'],
                        repoPath=config['repo_path'],
                        buildToolsRepoPath=config['build_tools_repo_path'],
                        buildSpace=0.5,
                        buildsBeforeReboot=pf['builds_before_reboot'],
                    )
                    packaged_unittest_builder = {
                        'name': '%s test %s' % (pf['base_name'], suites_name),
                        'slavenames': pf['slaves'],
                        'builddir': '%s-%s-unittest-%s' % (name, platform, suites_name),
                        'factory': packaged_unittest_factory,
                        'category': name,
                    }
                    branchObjects['builders'].append(packaged_unittest_builder)

        if config['enable_codecoverage']:
            # We only do code coverage builds on linux right now
            if platform == 'linux':
                codecoverage_factory = CodeCoverageFactory(
                    platform=platform,
                    productName=config['product_name'],
                    config_repo_path=config['config_repo_path'],
                    config_dir=config['config_subdir'],
                    objdir=config['objdir_unittests'],
                    hgHost=config['hghost'],
                    repoPath=config['repo_path'],
                    buildToolsRepoPath=config['build_tools_repo_path'],
                    buildSpace=5,
                    clobberURL=config['base_clobber_url'],
                    clobberTime=clobberTime,
                    buildsBeforeReboot=pf['builds_before_reboot'],
                    mochitest_leak_threshold=mochitestLeakThreshold,
                    crashtest_leak_threshold=crashtestLeakThreshold,
                    stageServer=config['stage_server'],
                    stageUsername=config['stage_username'],
                    stageSshKey=config['stage_ssh_key'],
                )
                codecoverage_builder = {
                    'name': '%s code coverage' % pf['base_name'],
                    'slavenames': pf['slaves'],
                    'builddir': '%s-%s-codecoverage' % (name, platform),
                    'factory': codecoverage_factory,
                    'category': name,
                }
                branchObjects['builders'].append(codecoverage_builder)

        if config['enable_shark'] and platform in ('macosx'):
             mozilla2_shark_factory = NightlyBuildFactory(
                 env= pf['env'],
                 objdir=config['objdir'],
                 platform=platform,
                 hgHost=config['hghost'],
                 repoPath=config['repo_path'],
                 buildToolsRepoPath=config['build_tools_repo_path'],
                 configRepoPath=config['config_repo_path'],
                 configSubDir=config['config_subdir'],
                 profiledBuild=False,
                 productName=config['product_name'],
                 mozconfig='macosx/%s/shark' % name,
                 stageServer=config['stage_server'],
                 stageUsername=config['stage_username'],
                 stageGroup=config['stage_group'],
                 stageSshKey=config['stage_ssh_key'],
                 stageBasePath=config['stage_base_path'],
                 codesighs=False,
                 uploadPackages=uploadPackages,
                 uploadSymbols=False,
                 nightly=True,
                 createSnippet=False,
                 buildSpace=buildSpace,
                 clobberURL=config['base_clobber_url'],
                 clobberTime=clobberTime,
                 buildsBeforeReboot=pf['builds_before_reboot']
             )
             mozilla2_shark_builder = {
                 'name': '%s shark' % pf['base_name'],
                 'slavenames': pf['slaves'],
                 'builddir': '%s-%s-shark' % (name, platform),
                 'factory': mozilla2_shark_factory,
                 'category': name,
             }
             branchObjects['builders'].append(mozilla2_shark_builder)

        if config['enable_xulrunner'] and platform not in ('wince'):
             xr_env = pf['env'].copy()
             xr_env['SYMBOL_SERVER_USER'] = config['stage_username_xulrunner']
             xr_env['SYMBOL_SERVER_PATH'] = config['symbol_server_xulrunner_path']
             xr_env['SYMBOL_SERVER_SSH_KEY'] = \
                 xr_env['SYMBOL_SERVER_SSH_KEY'].replace(config['stage_ssh_key'], config['stage_ssh_xulrunner_key'])
             mozilla2_xulrunner_factory = NightlyBuildFactory(
                 env=xr_env,
                 objdir=pf['platform_objdir'],
                 platform=platform,
                 hgHost=config['hghost'],
                 repoPath=config['repo_path'],
                 buildToolsRepoPath=config['build_tools_repo_path'],
                 configRepoPath=config['config_repo_path'],
                 configSubDir=config['config_subdir'],
                 profiledBuild=False,
                 productName='xulrunner',
                 mozconfig='%s/%s/xulrunner' % (platform, name),
                 stageServer=config['stage_server'],
                 stageUsername=config['stage_username_xulrunner'],
                 stageGroup=config['stage_group'],
                 stageSshKey=config['stage_ssh_xulrunner_key'],
                 stageBasePath=config['stage_base_path_xulrunner'],
                 codesighs=False,
                 uploadPackages=uploadPackages,
                 uploadSymbols=True,
                 nightly=True,
                 createSnippet=False,
                 buildSpace=buildSpace,
                 clobberURL=config['base_clobber_url'],
                 clobberTime=clobberTime,
                 buildsBeforeReboot=pf['builds_before_reboot'],
                 packageSDK=True,
             )
             mozilla2_xulrunner_builder = {
                 'name': '%s xulrunner' % pf['base_name'],
                 'slavenames': pf['slaves'],
                 'builddir': '%s-%s-xulrunner' % (name, platform),
                 'factory': mozilla2_xulrunner_factory,
                 'category': name,
             }
             branchObjects['builders'].append(mozilla2_xulrunner_builder)

    return branchObjects
