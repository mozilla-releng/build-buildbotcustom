from urlparse import urljoin
try:
    import json
    assert json  # pyflakes
except:
    import simplejson as json
import collections
import random
import re
import sys
import os
from copy import deepcopy

from twisted.python import log

from buildbot.scheduler import Nightly, Scheduler, Triggerable
from buildbot.schedulers.filter import ChangeFilter
from buildbot.steps.shell import WithProperties
from buildbot.status.builder import SUCCESS, WARNINGS, FAILURE, EXCEPTION, RETRY
from buildbot.process.buildstep import regex_log_evaluator

import buildbotcustom.common
import buildbotcustom.changes.hgpoller
import buildbotcustom.process.factory
import buildbotcustom.log
import buildbotcustom.l10n
import buildbotcustom.scheduler
import buildbotcustom.status.mail
import buildbotcustom.status.generators
import buildbotcustom.status.queued_command
import buildbotcustom.status.log_handlers
import buildbotcustom.misc_scheduler
import build.paths
import mozilla_buildtools.queuedir
reload(buildbotcustom.common)
reload(buildbotcustom.changes.hgpoller)
reload(buildbotcustom.process.factory)
reload(buildbotcustom.log)
reload(buildbotcustom.l10n)
reload(buildbotcustom.scheduler)
reload(buildbotcustom.status.mail)
reload(buildbotcustom.status.generators)
reload(buildbotcustom.status.log_handlers)
reload(buildbotcustom.misc_scheduler)
reload(build.paths)
reload(mozilla_buildtools.queuedir)

from buildbotcustom.common import normalizeName
from buildbotcustom.changes.hgpoller import HgPoller, HgAllLocalesPoller
from buildbotcustom.process.factory import NightlyBuildFactory, \
    NightlyRepackFactory, UnittestPackagedBuildFactory, TalosFactory, \
    TryBuildFactory, ScriptFactory, SigningScriptFactory, rc_eval_func
from buildbotcustom.process.factory import RemoteUnittestFactory
from buildbotcustom.scheduler import MultiScheduler, BuilderChooserScheduler, \
    PersistentScheduler, makePropertiesScheduler, SpecificNightly
from buildbotcustom.l10n import TriggerableL10n
from buildbotcustom.status.mail import MercurialEmailLookup, ChangeNotifier
from buildbotcustom.status.generators import buildTryChangeMessage
from buildbotcustom.env import MozillaEnvironments
from buildbotcustom.misc_scheduler import tryChooser, buildIDSchedFunc, \
    buildUIDSchedFunc, lastGoodFunc, lastRevFunc

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
            raise Exception('Found FIXME in %s for locale "%s"' %
                           (file, locale))
        locale = urljoin(l10nRepoPath, locale)
        repositories[locale] = {
            'revision': revision,
            'relbranchOverride': relbranch,
            'bumpFiles': []
        }

    return repositories


def get_locales_from_json(jsonFile, l10nRepoPath, relbranch):
    if not l10nRepoPath.endswith('/'):
        l10nRepoPath = l10nRepoPath + '/'

    l10nRepositories = {}
    platformLocales = collections.defaultdict(dict)

    file = open(jsonFile)
    localesJson = json.load(file)
    for locale in localesJson.keys():
        revision = localesJson[locale]['revision']
        if revision == 'FIXME':
            raise Exception('Found FIXME in %s for locale "%s"' %
                           (jsonFile, locale))
        localeUrl = urljoin(l10nRepoPath, locale)
        l10nRepositories[localeUrl] = {
            'revision': revision,
            'relbranchOverride': relbranch,
            'bumpFiles': []
        }
        for platform in localesJson[locale]['platforms']:
            platformLocales[platform][locale] = localesJson[
                locale]['platforms']

    return (l10nRepositories, platformLocales)

# This function is used as fileIsImportant parameter for Buildbots that do both
# dep/nightlies and release builds. Because they build the same "branch" this
# allows us to have the release builder ignore HgPoller triggered changse
# and the dep builders only obey HgPoller/Force Build triggered ones.


def isHgPollerTriggered(change, hgUrl):
    if (change.revlink and hgUrl in change.revlink) or \
            change.comments.find(hgUrl) > -1:
        return True


def shouldBuild(change):
    """check for commit message disabling build for this change"""
    return "DONTBUILD" not in change.comments


_product_excludes = {
    'firefox': [
        re.compile('^CLOBBER'),
        re.compile('^mobile/'),
        re.compile('^b2g/'),
    ],
    'mobile': [
        re.compile('^CLOBBER'),
        re.compile('^browser/'),
        re.compile('^b2g/'),
        re.compile('^xulrunner/'),
    ],
    'b2g': [
        re.compile('^CLOBBER'),
        re.compile('^browser/'),
        re.compile('^mobile/'),
        re.compile('^xulrunner/'),
    ],
    'thunderbird': [
        re.compile('^CLOBBER'),
        re.compile("^suite/")
    ],
}


def isImportantForProduct(change, product):
    """Handles product specific handling of important files"""
    # For each file, check each product's exclude list
    # If a file is not excluded, then the change is important
    # If all files are excluded, then the change is not important
    # As long as hgpoller's 'overflow' marker isn't excluded, it will cause all
    # products to build
    excludes = _product_excludes.get(product, [])
    for f in change.files:
        excluded = any(e.search(f) for e in excludes)
        if not excluded:
            log.msg("%s important for %s because of %s" % (
                change.revision, product, f))
            return True

    # Everything was excluded
    log.msg("%s not important for %s because all files were excluded" %
            (change.revision, product))
    return False


def makeImportantFunc(hgurl, product):
    def isImportant(c):
        if not isHgPollerTriggered(c, hgurl):
            return False
        if not shouldBuild(c):
            return False
        # No product is specified, so all changes are important
        if product is None:
            return True
        return isImportantForProduct(c, product)
    return isImportant


def isImportantL10nFile(change, l10nModules):
    for f in change.files:
        for basepath in l10nModules:
            if f.startswith(basepath):
                return True
    return False


def changeContainsProduct(change, productName):
    products = change.properties.getProperty("products")
    if isinstance(products, basestring) and \
            productName in products.split(','):
            return True
    return False


def changeBaseTagContainsScriptRepoRevision(change, baseTag):
    script_repo_revision = change.properties.getProperty("script_repo_revision")
    baseTag = baseTag + "_"
    if isinstance(script_repo_revision, basestring) and \
            baseTag in script_repo_revision:
            log.msg("baseTag '%s' IS in script_repo_revision '%s'" % (baseTag, script_repo_revision))
            return True
    log.msg("baseTag '%s' IS NOT in script_repo_revision '%s'" % (baseTag, script_repo_revision))
    return False


def changeContainsProperties(change, props={}):
    for prop, value in props.iteritems():
        if change.properties.getProperty(prop) != value:
            return False
    return True


def generateTestBuilderNames(name_prefix, suites_name, suites):
    test_builders = []
    if isinstance(suites, dict) and "totalChunks" in suites:
        totalChunks = suites['totalChunks']
        for i in range(totalChunks):
            test_builders.append('%s %s-%i' %
                                (name_prefix, suites_name, i + 1))
    else:
        test_builders.append('%s %s' % (name_prefix, suites_name))

    return test_builders

fastRegexes = []


def _partitionSlaves(slaves):
    """Partitions the list of slaves into 'fast' and 'slow' slaves, according
    to fastRegexes.
    Returns two lists, 'fast' and 'slow'."""
    fast = []
    slow = []
    for s in slaves:
        name = s.slave.slavename
        for e in fastRegexes:
            if re.search(e, name):
                fast.append(s)
                break
        else:
            slow.append(s)
    return fast, slow


def _getLastTimeOnBuilder(builder, slavename):
    # New builds are at the end of the buildCache, so
    # examine it backwards
    buildNumbers = reversed(sorted(builder.builder_status.buildCache.keys()))
    for buildNumber in buildNumbers:
        try:
            build = builder.builder_status.buildCache[buildNumber]
            # Skip non-successful builds
            if build.getResults() != 0:
                continue
            if build.slavename == slavename:
                return build.finished
        except KeyError:
            continue
    return None


def _recentSort(builder):
    def sortfunc(s1, s2):
        t1 = _getLastTimeOnBuilder(builder, s1.slave.slavename)
        t2 = _getLastTimeOnBuilder(builder, s2.slave.slavename)
        return cmp(t1, t2)
    return sortfunc


def _nextSlowSlave(builder, available_slaves):
    try:
        fast, slow = _partitionSlaves(available_slaves)
        # Choose the slow slave that was most recently on this builder
        # If there aren't any slow slaves, choose the slow slave that was most
        # recently on this builder
        if slow:
            return sorted(slow, _recentSort(builder))[-1]
        elif fast:
            return sorted(fast, _recentSort(builder))[-1]
        else:
            return None
    except:
        log.msg("Error choosing next slow slave for builder '%s', choosing randomly instead" % builder.name)
        log.err()
        return random.choice(available_slaves)


def _nextFastSlave(builder, available_slaves, only_fast=False):
    try:
        if only_fast:
            # Check that the builder has some fast slaves configured.  We do
            # this because some machines classes don't have a fast/slow
            # distinction, and so they default to 'slow'
            fast, slow = _partitionSlaves(builder.slaves)
            if not fast:
                log.msg("Builder '%s' has no fast slaves configured, but only_fast"
                        " is enabled; disabling only_fast" % builder.name)
                only_fast = False

        fast, slow = _partitionSlaves(available_slaves)

        # Choose the fast slave that was most recently on this builder
        # If there aren't any fast slaves, choose the slow slave that was most
        # recently on this builder if only_fast is False
        if not fast and only_fast:
            return None
        elif fast:
            return sorted(fast, _recentSort(builder))[-1]
        elif slow and not only_fast:
            return sorted(slow, _recentSort(builder))[-1]
        else:
            return None
    except:
        log.msg("Error choosing next fast slave for builder '%s', choosing randomly instead" % builder.name)
        log.err()
        return random.choice(available_slaves)


def _nextSlowIdleSlave(nReserved):
    """Return a nextSlave function that will only return a slave to run a build
    if there are at least nReserved slaves available."""
    def _nextslave(builder, available_slaves):
        fast, slow = _partitionSlaves(available_slaves)
        if len(slow) <= nReserved:
            return None
        return sorted(slow, _recentSort(builder))[-1]
    return _nextslave

# XXX Bug 790698 hack for no android reftests on new tegras
# Purge with fire when this is no longer needed


def _nextOldTegra(builder, available_slaves):
    try:
        valid = []
        for s in available_slaves:
            if 'panda-' in s.slave.slavename:
                # excempt Panda's from this foolishness
                valid.append(s)
                continue

            number = s.slave.slavename.replace('tegra-', '')
            try:
                if int(number) < 286:
                    valid.append(s)
            except ValueError:
                log.msg("Error parsing number out of '%s', discarding from old list" % s.slave.slavename)
                continue
        if valid:
            return random.choice(valid)
        return None
    except:
        log.msg("Error choosing old tegra for builder '%s', choosing randomly instead" % builder.name)
        log.err()
        return random.choice(available_slaves)

nomergeBuilders = []


def mergeRequests(builder, req1, req2):
    if builder.name in nomergeBuilders:
        return False
    if 'Self-serve' in req1.reason or 'Self-serve' in req2.reason:
        # A build was explicitly requested on this revision, so don't coalesce
        # it
        return False
    return req1.canBeMergedWith(req2)


def mergeBuildObjects(d1, d2):
    retval = d1.copy()
    keys = ['builders', 'status', 'schedulers', 'change_source']

    for key in keys:
        retval.setdefault(key, []).extend(d2.get(key, []))

    return retval


def makeMHFactory(config, pf, **kwargs):
    factory_class = ScriptFactory
    if 'signingServers' in kwargs:
        if kwargs['signingServers'] is not None:
            factory_class = SigningScriptFactory
        else:
            del kwargs['signingServers']

    factory = factory_class(
        scriptRepo='%s%s' % (config['hgurl'],
                             config['mozharness_repo_path']),
        scriptName=pf['mozharness_config']['script_name'],
        extra_args=pf['mozharness_config'].get('extra_args'),
        reboot_command=pf['mozharness_config'].get('reboot_command'),
        script_timeout=pf.get('timeout', 3600),
        script_maxtime=pf.get('maxTime', 4 * 3600),
        **kwargs
    )
    return factory


def makeBundleBuilder(config, name):
    stageBasePath = '%s/%s' % (config['stage_base_path'],
                               config['platforms']['linux']['stage_product'])
    bundle_factory = ScriptFactory(
        config['hgurl'] + config['build_tools_repo_path'],
        'scripts/bundle/hg-bundle.sh',
        interpreter='bash',
        script_timeout=3600,
        script_maxtime=3600,
        extra_args=[
            name,
            config['repo_path'],
            config['stage_server'],
            config['stage_username'],
            stageBasePath,
            config['stage_ssh_key'],
        ],
    )
    slaves = set()
    for p in sorted(config['platforms'].keys()):
        slaves.update(set(config['platforms'][p]['slaves']))
    bundle_builder = {
        'name': '%s hg bundle' % name,
        'slavenames': list(slaves),
        'builddir': '%s-bundle' % (name,),
        'slavebuilddir': normalizeName('%s-bundle' % (name,)),
        'factory': bundle_factory,
        'category': name,
        'nextSlave': _nextSlowSlave,
        'properties': {'slavebuilddir': normalizeName('%s-bundle' % (name,)),
                       'branch': name,
                       'platform': None,
                       'product': 'firefox',
                       }
    }
    return bundle_builder


def generateTestBuilder(config, branch_name, platform, name_prefix,
                        build_dir_prefix, suites_name, suites,
                        mochitestLeakThreshold, crashtestLeakThreshold,
                        slaves=None, resetHwClock=False, category=None,
                        stagePlatform=None, stageProduct=None,
                        mozharness=False, mozharness_python=None,
                        mozharness_suite_config=None,
                        mozharness_repo=None, mozharness_tag='production'):
    builders = []
    pf = config['platforms'].get(platform, {})
    if slaves is None:
        slavenames = config['platforms'][platform]['slaves']
    else:
        slavenames = slaves
    if not category:
        category = branch_name
    productName = pf['product_name']
    branchProperty = branch_name
    posixBinarySuffix = '' if 'mobile' in name_prefix else '-bin'
    properties = {'branch': branchProperty, 'platform': platform,
                  'slavebuilddir': 'test', 'stage_platform': stagePlatform,
                  'product': stageProduct, 'repo_path': config['repo_path']}
    if mozharness:
        # suites is a dict!
        if mozharness_suite_config is None:
            mozharness_suite_config = {}
        extra_args = []
        if mozharness_suite_config.get('config_files'):
            extra_args.extend(['--cfg', ','.join(mozharness_suite_config['config_files'])])
        extra_args.extend(mozharness_suite_config.get('extra_args', suites.get('extra_args', [])))
        if mozharness_suite_config.get('download_symbols'):
            extra_args.extend(['--download-symbols', mozharness_suite_config['download_symbols']])
        reboot_command = mozharness_suite_config.get(
            'reboot_command', suites.get('reboot_command', None))
        hg_bin = mozharness_suite_config.get(
            'hg_bin', suites.get('hg_bin', 'hg'))
        properties['script_repo_revision'] = mozharness_tag
        factory = ScriptFactory(
            interpreter=mozharness_python,
            scriptRepo=mozharness_repo,
            scriptName=suites['script_path'],
            hg_bin=hg_bin,
            extra_args=extra_args,
            script_maxtime=suites.get('script_maxtime', 7200),
            reboot_command=reboot_command,
            platform=platform,
            env=mozharness_suite_config.get('env', {}),
            log_eval_func=rc_eval_func({
                0: SUCCESS,
                1: WARNINGS,
                2: FAILURE,
                3: EXCEPTION,
                4: RETRY,
            }),
        )
        builder = {
            'name': '%s %s' % (name_prefix, suites_name),
            'slavenames': slavenames,
            'builddir': '%s-%s' % (build_dir_prefix, suites_name),
            'slavebuilddir': 'test',
            'factory': factory,
            'category': category,
            'properties': properties,
        }
        builders.append(builder)
    elif pf.get('is_remote', False):
        hostUtils = pf['host_utils_url']
        factory = RemoteUnittestFactory(
            platform=platform,
            productName=productName,
            hostUtils=hostUtils,
            suites=suites,
            hgHost=config['hghost'],
            repoPath=config['repo_path'],
            buildToolsRepoPath=config['build_tools_repo_path'],
            branchName=branch_name,
            remoteExtras=pf.get('remote_extras'),
            # NB. If you change the defaults here, make sure to update the
            # logic in generateTalosBranchObjects for test_type == "debug"
            downloadSymbols=pf.get('download_symbols', False),
            downloadSymbolsOnDemand=pf.get('download_symbols_ondemand', True),
        )
        builder = {
            'name': '%s %s' % (name_prefix, suites_name),
            'slavenames': slavenames,
            'builddir': '%s-%s' % (build_dir_prefix, suites_name),
            'slavebuilddir': 'test',
            'factory': factory,
            'category': category,
            'properties': properties,
        }
        # XXX Bug 790698 hack for no android reftests on new tegras
        # Purge with fire when this is no longer needed
        if 'reftest' in suites_name:
            builder['nextSlave'] = _nextOldTegra
        builders.append(builder)
    else:
        if isinstance(suites, dict) and "totalChunks" in suites:
            totalChunks = suites['totalChunks']
            for i in range(totalChunks):
                factory = UnittestPackagedBuildFactory(
                    platform=platform,
                    test_suites=[suites['suite']],
                    mochitest_leak_threshold=mochitestLeakThreshold,
                    crashtest_leak_threshold=crashtestLeakThreshold,
                    hgHost=config['hghost'],
                    repoPath=config['repo_path'],
                    productName=productName,
                    posixBinarySuffix=posixBinarySuffix,
                    buildToolsRepoPath=config['build_tools_repo_path'],
                    buildSpace=1.0,
                    buildsBeforeReboot=config[
                        'platforms'][platform]['builds_before_reboot'],
                    totalChunks=totalChunks,
                    thisChunk=i + 1,
                    chunkByDir=suites.get('chunkByDir'),
                    env=pf.get('unittest-env', {}),
                    # NB. If you change the defaults here, make sure to update the
                    # logic in generateTalosBranchObjects for test_type ==
                    # "debug"
                    downloadSymbols=pf.get('download_symbols', False),
                    downloadSymbolsOnDemand=pf.get(
                        'download_symbols_ondemand', True),
                    resetHwClock=resetHwClock,
                )
                builder = {
                    'name': '%s %s-%i' % (name_prefix, suites_name, i + 1),
                    'slavenames': slavenames,
                    'builddir': '%s-%s-%i' % (build_dir_prefix, suites_name, i + 1),
                    'slavebuilddir': 'test',
                    'factory': factory,
                    'category': category,
                    'properties': properties,
                    'env': MozillaEnvironments.get(config['platforms'][platform].get('env_name'), {}),
                }
                builders.append(builder)
        else:
            factory = UnittestPackagedBuildFactory(
                platform=platform,
                test_suites=suites,
                mochitest_leak_threshold=mochitestLeakThreshold,
                crashtest_leak_threshold=crashtestLeakThreshold,
                hgHost=config['hghost'],
                repoPath=config['repo_path'],
                productName=productName,
                posixBinarySuffix=posixBinarySuffix,
                buildToolsRepoPath=config['build_tools_repo_path'],
                buildSpace=1.0,
                buildsBeforeReboot=config['platforms'][
                    platform]['builds_before_reboot'],
                downloadSymbols=pf.get('download_symbols', False),
                downloadSymbolsOnDemand=pf.get(
                    'download_symbols_ondemand', True),
                env=pf.get('unittest-env', {}),
                resetHwClock=resetHwClock,
            )
            builder = {
                'name': '%s %s' % (name_prefix, suites_name),
                'slavenames': slavenames,
                'builddir': '%s-%s' % (build_dir_prefix, suites_name),
                'slavebuilddir': 'test',
                'factory': factory,
                'category': category,
                'properties': properties,
                'env': MozillaEnvironments.get(config['platforms'][platform].get('env_name'), {}),
            }
            builders.append(builder)
    return builders


def generateMozharnessTalosBuilder(platform, mozharness_repo, script_path,
                                   hg_bin, mozharness_python,
                                   reboot_command, extra_args=None,
                                   script_timeout=3600,
                                   script_maxtime=7200):
    if extra_args is None:
        extra_args = []
    return ScriptFactory(
        interpreter=mozharness_python,
        scriptRepo=mozharness_repo,
        scriptName=script_path,
        hg_bin=hg_bin,
        extra_args=extra_args,
        use_credentials_file=True,
        script_timeout=script_timeout,
        script_maxtime=script_maxtime,
        reboot_command=reboot_command,
        platform=platform,
        log_eval_func=lambda c, s: regex_log_evaluator(c, s, (
                                                       (re.compile('# TBPL WARNING #'), WARNINGS),
                        (re.compile('# TBPL FAILURE #'), FAILURE),
            (re.compile('# TBPL EXCEPTION #'), EXCEPTION),
            (re.compile('# TBPL RETRY #'), RETRY),
        ))
    )


def generateBranchObjects(config, name, secrets=None):
    """name is the name of branch which is usually the last part of the path
       to the repository. For example, 'mozilla-central', 'mozilla-aurora', or
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
    if secrets is None:
        secrets = {}
    # List of all the per-checkin builders
    builders = []
    # Which builders should we consider when looking at per-checkin results and
    # determining what revision we should do a nightly build on
    buildersForNightly = []
    buildersByProduct = {}
    nightlyBuilders = []
    xulrunnerNightlyBuilders = []
    periodicPgoBuilders = []
        # Only used for the 'periodic' strategy. rename to perodicPgoBuilders?
    weeklyBuilders = []
    # prettyNames is a mapping to pass to the try_parser for validation
    PRETTY_NAME = '%(basename)s %(trystatus)sbuild'
    NAME = '%(basename)s build'
    prettyNames = {}
    # These dicts provides mapping between en-US dep and nightly scheduler names
    # to l10n dep and l10n nightly scheduler names. It's filled out just below
    # here.
    l10nBuilders = {}
    l10nNightlyBuilders = {}
    pollInterval = config.get('pollInterval', 60)
    l10nPollInterval = config.get('l10nPollInterval', 5 * 60)

    # We only understand a couple PGO strategies
    assert config['pgo_strategy'] in ('per-checkin', 'periodic', 'try', None), \
        "%s is not an understood PGO strategy" % config['pgo_strategy']

    # This section is to make it easier to disable certain products.
    # Ideally we could specify a shorter platforms key on the branch,
    # but that doesn't work
    enabled_platforms = []
    for platform in sorted(config['platforms'].keys()):
        pf = config['platforms'][platform]
        if pf['stage_product'] in config['enabled_products']:
            enabled_platforms.append(platform)

    # generate a list of builders, nightly builders (names must be different)
    # for easy access
    for platform in enabled_platforms:
        pf = config['platforms'][platform]
        base_name = pf['base_name']

        # short-circuit the extra logic below for debug, l10n, pgo and nightly
        # builds these aren't required (yet) for mozharness builds
        if 'mozharness_config' in pf:
            if pf.get('enable_dep', True):
                buildername = '%s_dep' % pf['base_name']
                builders.append(buildername)
                if pf.get('consider_for_nightly', True):
                    buildersForNightly.append(buildername)
                buildersByProduct.setdefault(
                    pf['stage_product'], []).append(buildername)
                prettyNames[platform] = buildername

            if pf.get('enable_nightly'):
                buildername = '%s_nightly' % pf['base_name']
                nightlyBuilders.append(buildername)
            continue

        values = {'basename': base_name,
                  'trystatus': '' if pf.get('try_by_default', True) else 'try-nondefault ',
                  }
        pretty_name = PRETTY_NAME % values
        buildername = NAME % values

        if pf.get('enable_dep', True):
            builders.append(buildername)
            if pf.get('consider_for_nightly', True):
                buildersForNightly.append(buildername)
            buildersByProduct.setdefault(
                pf['stage_product'], []).append(buildername)
            prettyNames[platform] = pretty_name

        # Fill the l10n dep dict
        if config['enable_l10n'] and platform in config['l10n_platforms'] and \
                config['enable_l10n_onchange']:
                l10nBuilders[base_name] = {}
                l10nBuilders[base_name]['tree'] = config['l10n_tree']
                l10nBuilders[base_name]['l10n_builder'] = \
                    '%s %s %s l10n dep' % (pf['product_name'].capitalize(),
                    name, platform)
                l10nBuilders[base_name]['platform'] = platform
        # Check if branch wants nightly builds
        if config['enable_nightly']:
            if 'enable_nightly' in pf:
                do_nightly = pf['enable_nightly']
            else:
                do_nightly = True
        else:
            do_nightly = False

        # Check if platform as a PGO builder
        if config['pgo_strategy'] in ('periodic',) and platform in config['pgo_platforms']:
            periodicPgoBuilders.append('%s pgo-build' % pf['base_name'])
        elif config['pgo_strategy'] in ('try',) and platform in config['pgo_platforms']:
            builders.append('%s pgo-build' % pf['base_name'])
            buildersByProduct.setdefault(pf['stage_product'], []).append(
                '%s pgo-build' % pf['base_name'])

        if do_nightly:
            builder = '%s nightly' % base_name
            nightlyBuilders.append(builder)
            # Fill the l10nNightly dict
            if config['enable_l10n'] and platform in config['l10n_platforms']:
                l10nNightlyBuilders[builder] = {}
                l10nNightlyBuilders[builder]['tree'] = config['l10n_tree']
                l10nNightlyBuilders[builder]['l10n_builder'] = \
                    '%s %s %s l10n nightly' % (pf['product_name'].capitalize(),
                    name, platform)
                l10nNightlyBuilders[builder]['platform'] = platform
            if config['enable_valgrind'] and \
                    platform in config['valgrind_platforms']:
                nightlyBuilders.append('%s valgrind' % base_name)
        if config.get('enable_blocklist_update', False) and platform in ('linux',):
            weeklyBuilders.append('%s blocklist update' % base_name)
        if config.get('enable_hsts_update', False) and platform in ('linux64',):
            weeklyBuilders.append('%s hsts preload update' % base_name)
        if pf.get('enable_xulrunner', config['enable_xulrunner']):
            xulrunnerNightlyBuilders.append('%s xulrunner nightly' % base_name)

    if config['enable_weekly_bundle']:
        bundle_builder = makeBundleBuilder(config, name)
        branchObjects['builders'].append(bundle_builder)
        weeklyBuilders.append(bundle_builder['name'])

    # Try Server notifier
    if config.get('enable_mail_notifier'):
        packageUrl = config['package_url']
        packageDir = config['package_dir']

        if config.get('notify_real_author'):
            extraRecipients = []
            sendToInterestedUsers = True
        else:
            extraRecipients = config['email_override']
            sendToInterestedUsers = False

        # This notifies users as soon as we receive their push, and will let them
        # know where to find builds/logs
        branchObjects['status'].append(ChangeNotifier(
            fromaddr="tryserver@build.mozilla.org",
            lookup=MercurialEmailLookup(),
            relayhost="mail.build.mozilla.org",
            sendToInterestedUsers=sendToInterestedUsers,
            extraRecipients=extraRecipients,
            branches=[config['repo_path']],
            messageFormatter=lambda c: buildTryChangeMessage(c,
                                                             '/'.join([packageUrl, packageDir])),
        ))

    if config['enable_l10n']:
        l10n_builders = []
        for b in l10nBuilders:
            if config['enable_l10n_onchange']:
                l10n_builders.append(l10nBuilders[b]['l10n_builder'])
            l10n_builders.append(
                l10nNightlyBuilders['%s nightly' % b]['l10n_builder'])
        l10n_binaryURL = config['enUS_binaryURL']
        if l10n_binaryURL.endswith('/'):
            l10n_binaryURL = l10n_binaryURL[:-1]
        l10n_binaryURL += "-l10n"
        nomergeBuilders.extend(l10n_builders)

    tipsOnly = False
    maxChanges = 100
    if config.get('enable_try', False):
        # Pay attention to all branches for pushes to try
        repo_branch = None
    else:
        # Other branches should only pay attention to the default branch
        repo_branch = "default"

    branchObjects['change_source'].append(HgPoller(
        hgURL=config['hgurl'],
        branch=config.get("poll_repo", config['repo_path']),
        tipsOnly=tipsOnly,
        maxChanges=maxChanges,
        repo_branch=repo_branch,
        pollInterval=pollInterval,
    ))

    if config['enable_l10n'] and config['enable_l10n_onchange']:
        hg_all_locales_poller = HgAllLocalesPoller(hgURL=config['hgurl'],
                                                   repositoryIndex=config[
                                                   'l10n_repo_path'],
                                                   pollInterval=l10nPollInterval,
                                                   branch=name)
        hg_all_locales_poller.parallelRequests = 1
        branchObjects['change_source'].append(hg_all_locales_poller)

    # schedulers
    # this one gets triggered by the HG Poller
    # for Try we have a custom scheduler that can accept a function to read commit comments
    # in order to know what to schedule
    extra_args = {}
    extra_args['treeStableTimer'] = None
    if config.get('enable_try'):
        scheduler_class = makePropertiesScheduler(
            BuilderChooserScheduler, [buildUIDSchedFunc])
        extra_args['chooserFunc'] = tryChooser
        extra_args['numberOfBuildsToTrigger'] = 1
        extra_args['prettyNames'] = prettyNames
        extra_args['buildbotBranch'] = name
    else:
        scheduler_class = makePropertiesScheduler(
            Scheduler, [buildIDSchedFunc, buildUIDSchedFunc])

    if not config.get('enable_merging', True):
        nomergeBuilders.extend(builders)
    # these should never, ever merge
    nomergeBuilders.extend(periodicPgoBuilders)

    if 'product_prefix' in config:
        scheduler_name_prefix = "%s_%s" % (config['product_prefix'], name)
    else:
        scheduler_name_prefix = name

    for product, product_builders in buildersByProduct.items():
        if config.get('enable_try'):
            fileIsImportant = lambda c: isHgPollerTriggered(c, config['hgurl'])
        else:
            # The per-produt build behaviour is tweakable per branch. If it's
            # not enabled, pass None as the product, which disables the
            # per-product build behaviour.
            if not config.get('enable_perproduct_builds'):
                fileIsImportant = makeImportantFunc(config['hgurl'], None)
            else:
                fileIsImportant = makeImportantFunc(config['hgurl'], product)

        branchObjects['schedulers'].append(scheduler_class(
            name=scheduler_name_prefix + "-" + product,
            branch=config.get("poll_repo", config['repo_path']),
            builderNames=product_builders,
            fileIsImportant=fileIsImportant,
            **extra_args
        ))

    if config['enable_l10n']:
        l10n_builders = []
        for b in l10nBuilders:
            l10n_builders.append(l10nBuilders[b]['l10n_builder'])
        # This L10n scheduler triggers only the builders of its own branch
        branchObjects['schedulers'].append(Scheduler(
            name="%s l10n" % name,
            branch=config['l10n_repo_path'],
            treeStableTimer=None,
            builderNames=l10n_builders,
            fileIsImportant=lambda c: isImportantL10nFile(
                c, config['l10n_modules']),
            properties={
                'app': 'browser',
                'en_revision': 'default',
            }
        ))

    # Now, setup the nightly en-US schedulers and maybe,
    # their downstream l10n ones
    if nightlyBuilders or xulrunnerNightlyBuilders:
        if config.get('enable_nightly_lastgood', True):
            goodFunc = lastGoodFunc(
                branch=config['repo_path'],
                builderNames=buildersForNightly,
                triggerBuildIfNoChanges=False,
                l10nBranch=config.get('l10n_repo_path')
            )
        else:
            goodFunc = lastRevFunc(
                config['repo_path'], triggerBuildIfNoChanges=True)

        nightly_scheduler = makePropertiesScheduler(
            SpecificNightly,
            [buildIDSchedFunc, buildUIDSchedFunc])(
                name="%s nightly" % scheduler_name_prefix,
                ssFunc=goodFunc,
                branch=config['repo_path'],
                # bug 482123 - keep the minute to avoid problems with DST
                # changes
                hour=config['start_hour'], minute=config['start_minute'],
                builderNames=nightlyBuilders + xulrunnerNightlyBuilders,
            )
        branchObjects['schedulers'].append(nightly_scheduler)

    if len(periodicPgoBuilders) > 0:
        pgo_scheduler = makePropertiesScheduler(
            SpecificNightly,
            [buildIDSchedFunc, buildUIDSchedFunc])(
                ssFunc=lastRevFunc(config['repo_path'],
                                   triggerBuildIfNoChanges=False),
                name="%s pgo" % scheduler_name_prefix,
                branch=config['repo_path'],
                builderNames=periodicPgoBuilders,
                hour=range(0, 24, config['periodic_pgo_interval']),
            )
        branchObjects['schedulers'].append(pgo_scheduler)

    for builder in nightlyBuilders + xulrunnerNightlyBuilders:
        if config['enable_l10n'] and \
                config['enable_nightly'] and builder in l10nNightlyBuilders:
            l10n_builder = l10nNightlyBuilders[builder]['l10n_builder']
            platform = l10nNightlyBuilders[builder]['platform']
            branchObjects['schedulers'].append(TriggerableL10n(
                                               name=l10n_builder,
                                               platform=platform,
                                               builderNames=[l10n_builder],
                                               branch=config['repo_path'],
                                               baseTag='default',
                                               localesURL=config.get(
                                               'localesURL', None)
                                               ))

    if weeklyBuilders:
        weekly_scheduler = Nightly(
            name='weekly-%s' % scheduler_name_prefix,
            branch=config['repo_path'],
            dayOfWeek=5,  # Saturday
            hour=[3], minute=[02],
            builderNames=weeklyBuilders,
        )
        branchObjects['schedulers'].append(weekly_scheduler)

    # We iterate throught the platforms a second time, so we need
    # to ensure that disabled platforms aren't configured the second time
    enabled_platforms = []
    for platform in sorted(config['platforms'].keys()):
        pf = config['platforms'][platform]
        if pf['stage_product'] in config['enabled_products']:
            enabled_platforms.append(platform)

    for platform in enabled_platforms:
        # shorthand
        pf = config['platforms'][platform]

        if 'mozharness_config' in pf:
            factory = makeMHFactory(config, pf, signingServers=secrets.get(
                pf.get('dep_signing_servers')))
            builder = {
                'name': '%s_dep' % pf['base_name'],
                'slavenames': pf['slaves'],
                'builddir': '%s_dep' % pf['base_name'],
                'slavebuilddir': normalizeName('%s_dep' % pf['base_name']),
                'factory': factory,
                'category': name,
                'properties': {
                    'branch': name,
                    'platform': platform,
                    'product': pf['stage_product'],
                    'repo_path': config['repo_path'],
                    'script_repo_revision': config['mozharness_tag'],
                }
            }
            if pf.get('enable_dep', True):
                branchObjects['builders'].append(builder)

            if pf.get('enable_nightly'):
                if pf.get('dep_signing_servers') != pf.get('nightly_signing_servers'):
                    # We need a new factory for this because our signing
                    # servers are different
                    factory = makeMHFactory(config, pf, signingServers=secrets.get(pf.get('nightly_signing_servers')))

                builder = {
                    'name': '%s_nightly' % pf['base_name'],
                    'slavenames': pf['slaves'],
                    'builddir': '%s_nightly' % pf['base_name'],
                    'slavebuilddir': normalizeName('%s_nightly' % pf['base_name']),
                    'factory': factory,
                    'category': name,
                    'properties': {
                        'branch': name,
                        'platform': platform,
                        'product': pf['stage_product'],
                        'repo_path': config['repo_path'],
                        'nightly_build': True,
                        'script_repo_revision': config['mozharness_tag'],
                    }
                }
                branchObjects['builders'].append(builder)
            # Nothing else to do for this builder
            continue

        # The stage platform needs to be used by the factory __init__ methods
        # as well as the log handler status target.  Instead of repurposing the
        # platform property on each builder, we will create a new property
        # on the needed builders
        stage_platform = pf.get('stage_platform', platform)

        uploadPackages = pf.get('uploadPackages', True)
        uploadSymbols = False
        disableSymbols = pf.get('disable_symbols', False)
        talosMasters = pf.get('talos_masters', [])
        unittestBranch = "%s-%s-unittest" % (name, pf.get('unittest_platform', platform))
        # Generate the PGO branch even if it isn't on for dep builds
        # because we will still use it for nightlies... maybe
        pgoUnittestBranch = "%s-%s-pgo-unittest" % (name, platform)
        tinderboxBuildsDir = None

        leakTest = pf.get('enable_leaktests', False)

        # Allow for test packages on platforms that can't be tested
        # on the same master.
        packageTests = pf.get('packageTests', False)

        doBuildAnalysis = pf.get('enable_build_analysis', False)

        buildSpace = pf.get('build_space', config['default_build_space'])
        l10nSpace = config['default_l10n_space']
        clobberTime = pf.get('clobber_time', config['default_clobber_time'])
        checkTest = pf.get('enable_checktests', False)
        valgrindCheck = pf.get('enable_valgrind_checktests', False)
        # Turn pymake on by default for Windows, and off by default for
        # other platforms.
        if 'win' in platform:
            enable_pymake = pf.get('enable_pymake', True)
        else:
            enable_pymake = pf.get('enable_pymake', False)

        extra_args = {}
        if config.get('enable_try'):
            factory_class = TryBuildFactory
            extra_args['packageUrl'] = config['package_url']
            extra_args['packageDir'] = config['package_dir']
            extra_args['branchName'] = name
            uploadSymbols = pf.get('upload_symbols', False)
        else:
            factory_class = NightlyBuildFactory
            uploadSymbols = False

        stageBasePath = '%s/%s' % (config['stage_base_path'],
                                   pf['stage_product'])

        # For the 'per-checkin' pgo strategy, we want PGO
        # enabled on what would be 'opt' builds.
        if platform in config['pgo_platforms']:
            if config['pgo_strategy'] in ('periodic', 'try') or config['pgo_strategy'] is None:
                per_checkin_build_uses_pgo = False
            elif config['pgo_strategy'] == 'per-checkin':
                per_checkin_build_uses_pgo = True
        else:
            # All platforms that can't do PGO... shouldn't do PGO.
            per_checkin_build_uses_pgo = False

        if per_checkin_build_uses_pgo:
            per_checkin_unittest_branch = pgoUnittestBranch
        else:
            per_checkin_unittest_branch = unittestBranch

        if config.get('mozilla_dir'):
            extra_args['mozillaDir'] = config['mozilla_dir']
        if config.get('leak_target'):
            extra_args['leakTarget'] = config['leak_target']

        multiargs = {}
        if pf.get('product_name') == 'b2g':
            multiargs[
                'multiLocaleScript'] = 'scripts/b2g_desktop_multilocale.py'
        else:
            if 'android' in platform:
                multiargs['multiLocaleScript'] = 'scripts/multil10n.py'
        if pf.get('multi_config_name'):
            multiargs['multiLocaleConfig'] = pf['multi_config_name']
        else:
            multiargs['multiLocaleConfig'] = 'multi_locale/%s_%s.json' % (
                name, platform)
        if config.get('enable_multi_locale') and pf.get('multi_locale'):
            multiargs['multiLocale'] = True
            multiargs['multiLocaleMerge'] = config['multi_locale_merge']
            multiargs['compareLocalesRepoPath'] = config[
                'compare_locales_repo_path']
            multiargs['compareLocalesTag'] = config['compare_locales_tag']
            multiargs['mozharnessMultiOptions'] = pf.get(
                'mozharness_multi_options')

        # Some platforms shouldn't do dep builds (i.e. RPM)
        if pf.get('enable_dep', True):
            factory_kwargs = {
                'env': pf['env'],
                'objdir': pf['platform_objdir'],
                'platform': platform,
                'hgHost': config['hghost'],
                'repoPath': config['repo_path'],
                'buildToolsRepoPath': config['build_tools_repo_path'],
                'configRepoPath': config['config_repo_path'],
                'configSubDir': config['config_subdir'],
                'profiledBuild': per_checkin_build_uses_pgo,
                'productName': pf['product_name'],
                'mozconfig': pf['mozconfig'],
                'srcMozconfig': pf.get('src_mozconfig'),
                'use_mock': pf.get('use_mock'),
                'mock_target': pf.get('mock_target'),
                'mock_packages': pf.get('mock_packages'),
                'mock_copyin_files': pf.get('mock_copyin_files'),
                'stageServer': config['stage_server'],
                'stageUsername': config['stage_username'],
                'stageGroup': config['stage_group'],
                'stageSshKey': config['stage_ssh_key'],
                'stageBasePath': stageBasePath,
                'stageLogBaseUrl': config.get('stage_log_base_url', None),
                'stagePlatform': pf['stage_platform'],
                'stageProduct': pf['stage_product'],
                'graphServer': config['graph_server'],
                'graphSelector': config['graph_selector'],
                'graphBranch': config.get('graph_branch', config.get('tinderbox_tree', None)),
                'doBuildAnalysis': doBuildAnalysis,
                'baseName': pf['base_name'],
                'leakTest': leakTest,
                'checkTest': checkTest,
                'valgrindCheck': valgrindCheck,
                'uploadPackages': uploadPackages,
                'uploadSymbols': uploadSymbols,
                'disableSymbols': disableSymbols,
                'buildSpace': buildSpace,
                'clobberURL': config['base_clobber_url'],
                'clobberTime': clobberTime,
                'buildsBeforeReboot': pf['builds_before_reboot'],
                'talosMasters': talosMasters,
                'enablePackaging': pf.get('enable_packaging', True),
                'enableInstaller': pf.get('enable_installer', False),
                'packageTests': packageTests,
                'unittestMasters': pf.get('unittest_masters', config['unittest_masters']),
                'unittestBranch': per_checkin_unittest_branch,
                'tinderboxBuildsDir': tinderboxBuildsDir,
                'enable_ccache': pf.get('enable_ccache', False),
                'useSharedCheckouts': pf.get('enable_shared_checkouts', False),
                'testPrettyNames': pf.get('test_pretty_names', False),
                'l10nCheckTest': pf.get('l10n_check_test', False),
                'post_upload_include_platform': pf.get('post_upload_include_platform', False),
                'signingServers': secrets.get(pf.get('dep_signing_servers')),
                'baseMirrorUrls': config.get('base_mirror_urls'),
                'baseBundleUrls': config.get('base_bundle_urls'),
                'mozillaDir': config.get('mozilla_dir', None),
                'tooltool_manifest_src': pf.get('tooltool_manifest_src', None),
                'tooltool_url_list': config.get('tooltool_url_list', []),
                'runAliveTests': pf.get('run_alive_tests', True),
                'gaiaRepo': pf.get('gaia_repo'),
                'gaiaRevision': config.get('gaia_revision'),
                'gaiaRevisionFile': pf.get('gaia_revision_file'),
                'gaiaLanguagesFile': pf.get('gaia_languages_file'),
                'gaiaLanguagesScript': pf.get('gaia_languages_script', 'scripts/b2g_desktop_multilocale.py'),
                'gaiaL10nRoot': config.get('gaia_l10n_root'),
                'mozharnessRepoPath': config.get('mozharness_repo_path'),
                'mozharnessTag': config.get('mozharness_tag'),
                'geckoL10nRoot': config.get('gecko_l10n_root'),
                'geckoLanguagesFile': pf.get('gecko_languages_file'),
                'runMakeAliveTests': config.get('run_make_alive_tests'),
                'enable_pymake': enable_pymake,
            }
            factory_kwargs.update(extra_args)
            if pf.get('product_name') == 'b2g':
                factory_kwargs.update(multiargs)

            mozilla2_dep_factory = factory_class(**factory_kwargs)
            # eg. TB Linux comm-central build
            #    TB Linux comm-central leak test build
            mozilla2_dep_builder = {
                'name': '%s build' % pf['base_name'],
                'slavenames': pf['slaves'],
                'builddir': '%s-%s' % (name, platform),
                'slavebuilddir': normalizeName('%s-%s' % (name, platform), pf['stage_product']),
                'factory': mozilla2_dep_factory,
                'category': name,
                'nextSlave': _nextFastSlave,
                # Uncomment to enable only fast slaves for dep builds.
                #'nextSlave': lambda b, sl: _nextFastSlave(b, sl, only_fast=True),
                'properties': {'branch': name,
                               'platform': platform,
                               'stage_platform': stage_platform,
                               'product': pf['stage_product'],
                               'slavebuilddir': normalizeName('%s-%s' % (name, platform), pf['stage_product'])},
            }
            branchObjects['builders'].append(mozilla2_dep_builder)

            # We have some platforms which need to be built every X hours with PGO.
            # These builds are as close to regular dep builds as we can make them,
            # other than PGO
            if config['pgo_strategy'] in ('periodic', 'try') and platform in config['pgo_platforms']:
                pgo_kwargs = factory_kwargs.copy()
                pgo_kwargs["doPostLinkerSize"] = pf.get('enable_post_linker_size', False)
                pgo_kwargs['profiledBuild'] = True
                pgo_kwargs['stagePlatform'] += '-pgo'
                pgo_kwargs['unittestBranch'] = pgoUnittestBranch
                pgo_factory = factory_class(**pgo_kwargs)
                pgo_builder = {
                    'name': '%s pgo-build' % pf['base_name'],
                    'slavenames': pf['slaves'],
                    'builddir': '%s-%s-pgo' % (name, platform),
                    'slavebuilddir': normalizeName('%s-%s-pgo' % (name, platform), pf['stage_product']),
                    'factory': pgo_factory,
                    'category': name,
                    'nextSlave': _nextFastSlave,
                    'properties': {'branch': name,
                                   'platform': platform,
                                   'stage_platform': stage_platform + '-pgo',
                                   'product': pf['stage_product'],
                                   'slavebuilddir': normalizeName('%s-%s-pgo' % (name, platform), pf['stage_product'])},
                }
                branchObjects['builders'].append(pgo_builder)

        # skip nightlies for debug builds unless requested at platform level
        if platform.endswith("-debug") and not pf.get('enable_nightly'):
                continue

        if config['enable_nightly']:
            if 'enable_nightly' in pf:
                do_nightly = pf['enable_nightly']
            else:
                do_nightly = True
        else:
            do_nightly = False

        l10n_objdir = pf['platform_objdir']
        if do_nightly:
            nightly_builder = '%s nightly' % pf['base_name']

            platform_env = pf['env'].copy()
            if 'update_channel' in config and config.get('create_snippet'):
                platform_env['MOZ_UPDATE_CHANNEL'] = config['update_channel']

            triggeredSchedulers = None
            if config['enable_l10n'] and pf.get('is_mobile_l10n') and pf.get('l10n_chunks'):
                mobile_l10n_scheduler_name = '%s-%s-l10n' % (name, platform)
                mobile_l10n_builders = []
                builder_env = platform_env.copy()
                for n in range(1, int(pf['l10n_chunks']) + 1):
                    builddir = '%s-%s-l10n_%s' % (name, platform, str(n))
                    builderName = "%s l10n nightly %s/%s" % \
                        (pf['base_name'], n, pf['l10n_chunks'])
                    mobile_l10n_builders.append(builderName)
                    extra_args = ['--cfg',
                                  'single_locale/%s_%s.py' % (name, platform),
                                  '--total-chunks', str(pf['l10n_chunks']),
                                  '--this-chunk', str(n)]
                    signing_servers = secrets.get(
                        pf.get('nightly_signing_servers'))
                    factory = SigningScriptFactory(
                        signingServers=signing_servers,
                        scriptRepo='%s%s' % (config['hgurl'],
                                             config['mozharness_repo_path']),
                        scriptName='scripts/mobile_l10n.py',
                        extra_args=extra_args
                    )
                    slavebuilddir = normalizeName(builddir, pf['stage_product'])
                    branchObjects['builders'].append({
                        'name': builderName,
                        'slavenames': pf.get('slaves'),
                        'builddir': builddir,
                        'slavebuilddir': slavebuilddir,
                        'factory': factory,
                        'category': name,
                        'nextSlave': _nextSlowSlave,
                        'properties': {'branch': '%s' % config['repo_path'],
                                       'builddir': '%s-l10n_%s' % (builddir, str(n)),
                                       'stage_platform': stage_platform,
                                       'product': pf['stage_product'],
                                       'platform': platform,
                                       'slavebuilddir': slavebuilddir,
                                       'script_repo_revision': config['mozharness_tag'],
                                       },
                        'env': builder_env
                    })

                branchObjects["schedulers"].append(Triggerable(
                    name=mobile_l10n_scheduler_name,
                    builderNames=mobile_l10n_builders
                ))
                triggeredSchedulers = [mobile_l10n_scheduler_name]

            else:  # Non-mobile l10n is done differently at this time
                if config['enable_l10n'] and platform in config['l10n_platforms'] and \
                        nightly_builder in l10nNightlyBuilders:
                    triggeredSchedulers = [
                        l10nNightlyBuilders[nightly_builder]['l10n_builder']]

            create_snippet = config['create_snippet']
            if 'create_snippet' in pf and config['create_snippet']:
                create_snippet = pf.get('create_snippet')
            if create_snippet and 'android' in platform:
                # Ideally, this woud use some combination of product name and
                # stage_platform, but that can be done in a follow up.
                # Android doesn't create updates for all the branches that
                # Firefox desktop does.
                if config.get('create_mobile_snippet'):
                    ausargs = {
                        'downloadBaseURL': config['mobile_download_base_url'],
                        'downloadSubdir': '%s-%s' % (name, pf.get('stage_platform', platform)),
                        'ausBaseUploadDir': config['aus2_mobile_base_upload_dir'],
                    }
                else:
                    create_snippet = False
                    ausargs = {}
            else:
                ausargs = {
                    'downloadBaseURL': config['download_base_url'],
                    'downloadSubdir': '%s-%s' % (name, pf.get('stage_platform', platform)),
                    'ausBaseUploadDir': config['aus2_base_upload_dir'],
                }

            nightly_kwargs = {}
            nightly_kwargs.update(multiargs)
            nightly_kwargs.update(ausargs)

            # We make the assumption that *all* nightly builds
            # are to be done with PGO.  This is to ensure that all
            # branches get some PGO coverage
            # We do not stick '-pgo' in the stage_platform for
            # nightlies because it'd be ugly and break stuff
            if platform in config['pgo_platforms']:
                nightly_pgo = True
                nightlyUnittestBranch = pgoUnittestBranch
            else:
                nightlyUnittestBranch = unittestBranch
                nightly_pgo = False

            nightly_env = platform_env.copy()
            nightly_env['IS_NIGHTLY'] = "yes"

            mozilla2_nightly_factory = NightlyBuildFactory(
                env=nightly_env,
                objdir=pf['platform_objdir'],
                platform=platform,
                hgHost=config['hghost'],
                repoPath=config['repo_path'],
                buildToolsRepoPath=config['build_tools_repo_path'],
                configRepoPath=config['config_repo_path'],
                configSubDir=config['config_subdir'],
                profiledBuild=nightly_pgo,
                productName=pf['product_name'],
                mozconfig=pf['mozconfig'],
                srcMozconfig=pf.get('src_mozconfig'),
                use_mock=pf.get('use_mock'),
                mock_target=pf.get('mock_target'),
                mock_packages=pf.get('mock_packages'),
                mock_copyin_files=pf.get('mock_copyin_files'),
                stageServer=config['stage_server'],
                stageUsername=config['stage_username'],
                stageGroup=config['stage_group'],
                stageSshKey=config['stage_ssh_key'],
                stageBasePath=stageBasePath,
                stageLogBaseUrl=config.get('stage_log_base_url', None),
                stagePlatform=pf['stage_platform'],
                stageProduct=pf['stage_product'],
                doBuildAnalysis=doBuildAnalysis,
                uploadPackages=uploadPackages,
                uploadSymbols=pf.get('upload_symbols', False),
                disableSymbols=pf.get('disable_symbols', False),
                nightly=True,
                createSnippet=create_snippet,
                createPartial=pf.get(
                    'create_partial', config['create_partial']),
                updatePlatform=pf['update_platform'],
                ausUser=config['aus2_user'],
                ausSshKey=config['aus2_ssh_key'],
                ausHost=config['aus2_host'],
                hashType=config['hash_type'],
                balrog_api_root=config.get('balrog_api_root', None),
                balrog_credentials_file=config['balrog_credentials_file'],
                buildSpace=buildSpace,
                clobberURL=config['base_clobber_url'],
                clobberTime=clobberTime,
                buildsBeforeReboot=pf['builds_before_reboot'],
                talosMasters=talosMasters,
                enablePackaging=pf.get('enable_packaging', True),
                enableInstaller=pf.get('enable_installer', False),
                packageTests=packageTests,
                unittestMasters=pf.get(
                    'unittest_masters', config['unittest_masters']),
                unittestBranch=nightlyUnittestBranch,
                triggerBuilds=config['enable_l10n'],
                triggeredSchedulers=triggeredSchedulers,
                tinderboxBuildsDir=tinderboxBuildsDir,
                enable_ccache=pf.get('enable_ccache', False),
                useSharedCheckouts=pf.get('enable_shared_checkouts', False),
                testPrettyNames=pf.get('test_pretty_names', False),
                l10nCheckTest=pf.get('l10n_check_test', False),
                post_upload_include_platform=pf.get(
                    'post_upload_include_platform', False),
                signingServers=secrets.get(pf.get('nightly_signing_servers')),
                baseMirrorUrls=config.get('base_mirror_urls'),
                baseBundleUrls=config.get('base_bundle_urls'),
                mozillaDir=config.get('mozilla_dir', None),
                tooltool_manifest_src=pf.get('tooltool_manifest_src', None),
                tooltool_url_list=config.get('tooltool_url_list', []),
                gaiaRepo=pf.get('gaia_repo'),
                gaiaRevision=config.get('gaia_revision'),
                gaiaRevisionFile=pf.get('gaia_revision_file'),
                gaiaLanguagesFile=pf.get('gaia_languages_file'),
                gaiaLanguagesScript=pf.get('gaia_languages_script', 'scripts/b2g_desktop_multilocale.py'),
                gaiaL10nRoot=config.get('gaia_l10n_root'),
                mozharnessRepoPath=config.get('mozharness_repo_path'),
                mozharnessTag=config.get('mozharness_tag'),
                geckoL10nRoot=config.get('gecko_l10n_root'),
                geckoLanguagesFile=pf.get('gecko_languages_file'),
                enable_pymake=enable_pymake,
                runMakeAliveTests=config.get('run_make_alive_tests'),
                **nightly_kwargs
            )

            # eg. TB Linux comm-aurora nightly
            mozilla2_nightly_builder = {
                'name': nightly_builder,
                'slavenames': pf['slaves'],
                'builddir': '%s-%s-nightly' % (name, platform),
                'slavebuilddir': normalizeName('%s-%s-nightly' % (name, platform), pf['stage_product']),
                'factory': mozilla2_nightly_factory,
                'category': name,
                'nextSlave': lambda b, sl: _nextFastSlave(b, sl, only_fast=True),
                'properties': {'branch': name,
                               'platform': platform,
                               'stage_platform': stage_platform,
                               'product': pf['stage_product'],
                               'nightly_build': True,
                               'slavebuilddir': normalizeName('%s-%s-nightly' % (name, platform), pf['stage_product'])},
            }
            branchObjects['builders'].append(mozilla2_nightly_builder)

            if config['enable_l10n']:
                if platform in config['l10n_platforms']:
                    mozconfig = os.path.join(os.path.dirname(
                        pf['src_mozconfig']), 'l10n-mozconfig')
                    l10n_kwargs = {}
                    if config.get('call_client_py', False):
                        l10n_kwargs['callClientPy'] = True
                        l10n_kwargs['clientPyConfig'] = {
                            'chatzilla_repo_path': config.get('chatzilla_repo_path', ''),
                            'cvsroot': config.get('cvsroot', ''),
                            'inspector_repo_path': config.get('inspector_repo_path', ''),
                            'moz_repo_path': config.get('moz_repo_path', ''),
                            'skip_blank_repos': config.get('skip_blank_repos', False),
                            'venkman_repo_path': config.get('venkman_repo_path', ''),
                        }

                    mozilla2_l10n_nightly_factory = NightlyRepackFactory(
                        env=platform_env,
                        objdir=l10n_objdir,
                        platform=platform,
                        hgHost=config['hghost'],
                        tree=config['l10n_tree'],
                        project=pf['product_name'],
                        appName=pf['app_name'],
                        enUSBinaryURL=config['enUS_binaryURL'],
                        nightly=True,
                        mozconfig=mozconfig,
                        l10nNightlyUpdate=config['l10nNightlyUpdate'],
                        l10nDatedDirs=config['l10nDatedDirs'],
                        createPartial=config['create_partial_l10n'],
                        ausBaseUploadDir=config['aus2_base_upload_dir_l10n'],
                        updatePlatform=pf['update_platform'],
                        downloadBaseURL=config['download_base_url'],
                        ausUser=config['aus2_user'],
                        ausSshKey=config['aus2_ssh_key'],
                        ausHost=config['aus2_host'],
                        balrog_api_root=config.get('balrog_api_root', None),
                        balrog_credentials_file=config[
                            'balrog_credentials_file'],
                        hashType=config['hash_type'],
                        stageServer=config['stage_server'],
                        stageUsername=config['stage_username'],
                        stageSshKey=config['stage_ssh_key'],
                        repoPath=config['repo_path'],
                        l10nRepoPath=config['l10n_repo_path'],
                        buildToolsRepoPath=config['build_tools_repo_path'],
                        compareLocalesRepoPath=config[
                            'compare_locales_repo_path'],
                        compareLocalesTag=config['compare_locales_tag'],
                        buildSpace=l10nSpace,
                        clobberURL=config['base_clobber_url'],
                        clobberTime=clobberTime,
                        mozillaDir=config.get('mozilla_dir', None),
                        signingServers=secrets.get(
                            pf.get('nightly_signing_servers')),
                        baseMirrorUrls=config.get('base_mirror_urls'),
                        extraConfigureArgs=config.get(
                            'l10n_extra_configure_args', []),
                        buildsBeforeReboot=pf.get('builds_before_reboot', 0),
                        use_mock=pf.get('use_mock'),
                        mock_target=pf.get('mock_target'),
                        mock_packages=pf.get('mock_packages'),
                        mock_copyin_files=pf.get('mock_copyin_files'),
                        enable_pymake=enable_pymake,
                        **l10n_kwargs
                    )
                    # eg. Thunderbird comm-aurora linux l10n nightly
                    slavebuilddir = normalizeName('%s-%s-l10n-nightly' % (name, platform), pf['stage_product'], max_=50)
                    mozilla2_l10n_nightly_builder = {
                        'name': l10nNightlyBuilders[nightly_builder]['l10n_builder'],
                        'slavenames': pf.get('l10n_slaves', pf['slaves']),
                        'builddir': '%s-%s-l10n-nightly' % (name, platform),
                        'slavebuilddir': slavebuilddir,
                        'factory': mozilla2_l10n_nightly_factory,
                        'category': name,
                        'nextSlave': _nextSlowSlave,
                        'properties': {'branch': name,
                                       'platform': platform,
                                       'product': pf['stage_product'],
                                       'stage_platform': stage_platform,
                                       'slavebuilddir': slavebuilddir},
                    }
                    branchObjects['builders'].append(
                        mozilla2_l10n_nightly_builder)

            if config['enable_valgrind'] and \
                    platform in config['valgrind_platforms']:
                valgrind_env = platform_env.copy()
                valgrind_env['REVISION'] = WithProperties("%(revision)s")
                mozilla2_valgrind_factory = ScriptFactory(
                    scriptRepo="%s%s" % (config['hgurl'],
                                         config['build_tools_repo_path']),
                    scriptName='scripts/valgrind/valgrind.sh',
                    use_mock=pf.get('use_mock'),
                    mock_target=pf.get('mock_target'),
                    mock_packages=pf.get('mock_packages'),
                    mock_copyin_files=pf.get('mock_copyin_files'),
                    env=valgrind_env,
                )
                mozilla2_valgrind_builder = {
                    'name': '%s valgrind' % pf['base_name'],
                    'slavenames': pf['slaves'],
                    'builddir': '%s-%s-valgrind' % (name, platform),
                    'slavebuilddir': normalizeName('%s-%s-valgrind' % (name, platform), pf['stage_product']),
                    'factory': mozilla2_valgrind_factory,
                    'category': name,
                    'env': valgrind_env,
                    'nextSlave': _nextSlowSlave,
                    'properties': {'branch': name,
                                   'platform': platform,
                                   'stage_platform': stage_platform,
                                   'product': pf['stage_product'],
                                   'slavebuilddir': normalizeName('%s-%s-valgrind' % (name, platform), pf['stage_product'])},
                }
                branchObjects['builders'].append(mozilla2_valgrind_builder)

        # We still want l10n_dep builds if nightlies are off
        if config['enable_l10n'] and platform in config['l10n_platforms'] and \
                config['enable_l10n_onchange']:
            dep_kwargs = {}
            if config.get('call_client_py', False):
                dep_kwargs['callClientPy'] = True
                dep_kwargs['clientPyConfig'] = {
                    'chatzilla_repo_path': config.get('chatzilla_repo_path', ''),
                    'cvsroot': config.get('cvsroot', ''),
                    'inspector_repo_path': config.get('inspector_repo_path', ''),
                    'moz_repo_path': config.get('moz_repo_path', ''),
                    'skip_blank_repos': config.get('skip_blank_repos', False),
                    'venkman_repo_path': config.get('venkman_repo_path', ''),
                }
            mozconfig = os.path.join(
                os.path.dirname(pf['src_mozconfig']), 'l10n-mozconfig')
            mozilla2_l10n_dep_factory = NightlyRepackFactory(
                env=platform_env,
                objdir=l10n_objdir,
                platform=platform,
                hgHost=config['hghost'],
                tree=config['l10n_tree'],
                project=pf['product_name'],
                appName=pf['app_name'],
                enUSBinaryURL=config['enUS_binaryURL'],
                mozillaDir=config.get('mozilla_dir', None),
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
                buildSpace=l10nSpace,
                clobberURL=config['base_clobber_url'],
                clobberTime=clobberTime,
                signingServers=secrets.get(pf.get('dep_signing_servers')),
                baseMirrorUrls=config.get('base_mirror_urls'),
                extraConfigureArgs=config.get('l10n_extra_configure_args', []),
                buildsBeforeReboot=pf.get('builds_before_reboot', 0),
                use_mock=pf.get('use_mock'),
                mock_target=pf.get('mock_target'),
                mock_packages=pf.get('mock_packages'),
                mock_copyin_files=pf.get('mock_copyin_files'),
                mozconfig=mozconfig,
                enable_pymake=enable_pymake,
                **dep_kwargs
            )
            # eg. Thunderbird comm-central linux l10n dep
            slavebuilddir = normalizeName('%s-%s-l10n-dep' % (name, platform), pf['stage_product'], max_=50)
            mozilla2_l10n_dep_builder = {
                'name': l10nBuilders[pf['base_name']]['l10n_builder'],
                'slavenames': pf.get('l10n_slaves', pf['slaves']),
                'builddir': '%s-%s-l10n-dep' % (name, platform),
                'slavebuilddir': slavebuilddir,
                'factory': mozilla2_l10n_dep_factory,
                'category': name,
                'nextSlave': _nextSlowSlave,
                'properties': {'branch': name,
                               'platform': platform,
                               'stage_platform': stage_platform,
                               'product': pf['stage_product'],
                               'slavebuilddir': slavebuilddir},
            }
            branchObjects['builders'].append(mozilla2_l10n_dep_builder)

        if config.get('enable_blocklist_update', False):
            if platform == 'linux':
                blocklistBuilder = generateBlocklistBuilder(
                    config, name, platform, pf['base_name'], pf['slaves'])
                branchObjects['builders'].append(blocklistBuilder)

        if config.get('enable_hsts_update', False):
            if platform == 'linux64':
                hstsBuilder = generateHSTSBuilder(
                    config, name, platform, pf['base_name'], pf['slaves'])
                branchObjects['builders'].append(hstsBuilder)

        if pf.get('enable_xulrunner', config['enable_xulrunner']):
            xr_env = pf['env'].copy()
            xr_env['SYMBOL_SERVER_USER'] = config['stage_username_xulrunner']
            xr_env['SYMBOL_SERVER_PATH'] = config[
                'symbol_server_xulrunner_path']
            xr_env['SYMBOL_SERVER_SSH_KEY'] = \
                xr_env['SYMBOL_SERVER_SSH_KEY'].replace(config['stage_ssh_key'], config['stage_ssh_xulrunner_key'])
            if 'xr_mozconfig' in pf:
                mozconfig = pf['xr_mozconfig']
            else:
                mozconfig = '%s/%s/xulrunner' % (platform, name)
            xulrunnerStageBasePath = '%s/xulrunner' % config['stage_base_path']
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
                mozconfig=mozconfig,
                srcMozconfig=pf.get('src_xulrunner_mozconfig'),
                stageServer=config['stage_server'],
                stageUsername=config['stage_username_xulrunner'],
                stageGroup=config['stage_group'],
                stageSshKey=config['stage_ssh_xulrunner_key'],
                stageBasePath=xulrunnerStageBasePath,
                uploadPackages=uploadPackages,
                uploadSymbols=True,
                nightly=True,
                createSnippet=False,
                buildSpace=buildSpace,
                clobberURL=config['base_clobber_url'],
                clobberTime=clobberTime,
                buildsBeforeReboot=pf['builds_before_reboot'],
                packageSDK=True,
                signingServers=secrets.get(pf.get('nightly_signing_servers')),
                tooltool_manifest_src=pf.get('tooltool_manifest_src', None),
                tooltool_url_list=config.get('tooltool_url_list', []),
                use_mock=pf.get('use_mock'),
                mock_target=pf.get('mock_target'),
                mock_packages=pf.get('mock_packages'),
                mock_copyin_files=pf.get('mock_copyin_files'),
                enable_pymake=enable_pymake,
            )
            mozilla2_xulrunner_builder = {
                'name': '%s xulrunner nightly' % pf['base_name'],
                'slavenames': pf['slaves'],
                'builddir': '%s-%s-xulrunner-nightly' % (name, platform),
                'slavebuilddir': normalizeName('%s-%s-xulrunner-nightly' % (name, platform), pf['stage_product']),
                'factory': mozilla2_xulrunner_factory,
                'category': name,
                'nextSlave': _nextSlowSlave,
                'properties': {'branch': name, 'platform': platform, 'slavebuilddir': normalizeName('%s-%s-xulrunner-nightly' % (name, platform)), 'product': 'xulrunner'},
            }
            branchObjects['builders'].append(mozilla2_xulrunner_builder)

        # -- end of per-platform loop --

    return branchObjects


def generateTalosBranchObjects(branch, branch_config, PLATFORMS, SUITES,
                               ACTIVE_UNITTEST_PLATFORMS, factory_class=TalosFactory):
    branchObjects = {'schedulers': [], 'builders': [], 'status': [],
                     'change_source': []}
    # prettyNames is a mapping to pass to the try_parser for validation
    prettyNames = {}

    # We only understand a couple PGO strategies
    assert branch_config['pgo_strategy'] in ('per-checkin', 'periodic', 'try', None), \
        "%s is not an understood PGO strategy" % branch_config[
        'pgo_strategy']

    buildBranch = branch_config['build_branch']

    for platform, platform_config in PLATFORMS.items():
        if 'platforms' in branch_config and \
           platform in branch_config['platforms'] and \
           not branch_config['platforms'][platform].get('enable_talos', True):
            continue

        if platform_config.get('is_mobile', False):
            branchName = branch_config['mobile_branch_name']
            talosBranch = branch_config.get(
                'mobile_talos_branch', branch_config['mobile_tinderbox_tree'])
        else:
            branchName = branch_config['branch_name']
            talosBranch = branch_config['tinderbox_tree']

        branchProperty = branch

        stage_platform = platform_config.get('stage_platform', platform)
        stage_product = platform_config['stage_product']

        # Decide whether this platform should have PGO builders created
        if branch_config['pgo_strategy'] and platform in branch_config['pgo_platforms']:
            create_pgo_builders = True
        else:
            create_pgo_builders = False

        # if platform is in the branch config check for overriding slave_platforms at the branch level
        # before creating the builders & schedulers
        if branch_config['platforms'].get(platform):
            slave_platforms = branch_config['platforms'][platform].get(
                'slave_platforms', platform_config.get('slave_platforms', []))
            talos_slave_platforms = branch_config['platforms'][platform].get(
                'talos_slave_platforms', platform_config.get('talos_slave_platforms', []))

            # Map of # of test runs to builder names
            talos_builders = {}
            talos_pgo_builders = {}

            try_default = True
            if not branch_config['platforms'][platform].get('try_by_default', True):
                try_default = False
            elif not platform_config.get('try_by_default', True):
                try_default = False

            for slave_platform in set(slave_platforms + talos_slave_platforms):
                platform_name = platform_config[slave_platform]['name']
                # this is to handle how a platform has more than one slave
                # platform
                slave_platform_try_default = try_default
                if not platform_config[slave_platform].get('try_by_default', True):
                    slave_platform_try_default = False
                platformPrettyName = platform_name
                if not slave_platform_try_default:
                    platformPrettyName += ' try-nondefault'
                prettyNames.setdefault(platform, []).append(platformPrettyName)
                for suite, talosConfig in SUITES.items():
                    tests, merge, extra, platforms = branch_config[
                        '%s_tests' % suite]
                    if tests == 0 or slave_platform not in platforms:
                        continue

                    # We only want to append '-Non-PGO' to platforms that
                    # also have PGO builds.
                    if create_pgo_builders:
                        opt_branch_name = branchName + '-Non-PGO'
                        opt_talos_branch = talosBranch + '-Non-PGO'
                    else:
                        opt_branch_name = branchName
                        opt_talos_branch = talosBranch

                    factory_kwargs = {
                        "OS": slave_platform,
                        "supportUrlBase": branch_config['support_url_base'],
                        "envName": platform_config['env_name'],
                        "workdirBase": "../talos-data",
                        "buildBranch": buildBranch,
                        "branchName": opt_branch_name,
                        "branch": branch,
                        "talosBranch": opt_talos_branch,
                        "configOptions": talosConfig,
                        "talosCmd": branch_config['talos_command'],
                        "fetchSymbols": branch_config['fetch_symbols'] and
                        platform_config[
                            slave_platform].get('download_symbols', True),
                        "talos_from_source_code": branch_config.get('talos_from_source_code', False),
                        "credentialsFile": os.path.join(os.getcwd(), "BuildSlaves.py"),
                        "datazillaUrl": branch_config.get('datazilla_url')
                    }
                    factory_kwargs.update(extra)

                    builddir = "%s_%s_test-%s" % (
                        branch, slave_platform, suite)
                    slavebuilddir = 'test'
                    properties = {
                        'branch': branchProperty,
                        'platform': slave_platform,
                        'stage_platform': stage_platform,
                        'product': stage_product,
                        'builddir': builddir,
                        'slavebuilddir': slavebuilddir,
                    }
                    if branch_config.get('mozharness_talos') and not platform_config.get('is_mobile'):
                        extra_args = ['--suite', suite,
                                      '--add-option',
                                      ','.join(['--webServer', 'localhost']),
                                      '--branch-name', opt_talos_branch]
                        if '64' in platform:
                            extra_args.extend(['--system-bits', '64'])
                        else:
                            extra_args.extend(['--system-bits', '32'])
                        if 'win' in platform:
                            extra_args.extend(
                                ['--cfg', 'talos/windows_config.py'])
                        elif 'mac' in platform:
                            extra_args.extend(['--cfg', 'talos/mac_config.py'])
                        else:
                            assert 'linux' in platform, "buildbotcustom.misc: mozharness talos: unknown platform %s!" % platform
                            extra_args.extend(
                                ['--cfg', 'talos/linux_config.py'])
                        if factory_kwargs['fetchSymbols']:
                            extra_args += ['--download-symbols', 'ondemand']
                        if factory_kwargs["talos_from_source_code"]:
                            extra_args.append('--use-talos-json')
                        factory = generateMozharnessTalosBuilder(
                            platform=platform,
                            mozharness_repo=branch_config['mozharness_repo'],
                            script_path="scripts/talos_script.py",
                            hg_bin=platform_config[
                                'mozharness_config']['hg_bin'],
                            mozharness_python=platform_config[
                                'mozharness_config']['mozharness_python'],
                            extra_args=extra_args,
                            script_timeout=platform_config[
                                'mozharness_config'].get('script_timeout', 3600),
                            script_maxtime=platform_config[
                                'mozharness_config'].get('script_maxtime', 7200),
                            reboot_command=platform_config[
                                'mozharness_config'].get('reboot_command'),
                        )
                        properties['script_repo_revision'] = branch_config['mozharness_tag']
                    else:
                        factory = factory_class(**factory_kwargs)

                    builder = {
                        'name': "%s %s talos %s" % (platform_name, branch, suite),
                        'slavenames': platform_config[slave_platform]['slaves'],
                        'builddir': builddir,
                        'slavebuilddir': slavebuilddir,
                        'factory': factory,
                        'category': branch,
                        'properties': properties,
                        'env': MozillaEnvironments[platform_config['env_name']],
                    }

                    if not merge:
                        nomergeBuilders.append(builder['name'])

                    talos_builders.setdefault(
                        tests, []).append(builder['name'])
                    branchObjects['builders'].append(builder)

                    if create_pgo_builders:
                        pgo_factory_kwargs = factory_kwargs.copy()
                        pgo_factory_kwargs['branchName'] = branchName
                        pgo_factory_kwargs['talosBranch'] = talosBranch
                        pgo_factory = factory_class(**pgo_factory_kwargs)
                        pgo_builder = {
                            'name': "%s %s pgo talos %s" % (platform_name, branch, suite),
                            'slavenames': platform_config[slave_platform]['slaves'],
                            'builddir': builddir + '-pgo',
                            'slavebuilddir': slavebuilddir + '-pgo',
                            'factory': pgo_factory,
                            'category': branch,
                            'properties': {
                                'branch': branchProperty,
                                'platform': slave_platform,
                                'stage_platform': stage_platform + '-pgo',
                                'product': stage_product,
                                'builddir': builddir,
                                'slavebuilddir': slavebuilddir,
                            },
                        }

                        if not merge:
                            nomergeBuilders.append(pgo_builder['name'])
                        branchObjects['builders'].append(pgo_builder)
                        talos_pgo_builders.setdefault(
                            tests, []).append(pgo_builder['name'])

                # Skip talos only platforms, not active platforms, branches
                # with disabled unittests
                if slave_platform in slave_platforms and platform in ACTIVE_UNITTEST_PLATFORMS.keys() \
                        and branch_config.get('enable_unittests', True) and slave_platform in branch_config['platforms'][platform]:
                    testTypes = []
                    # unittestSuites are gathered up for each platform from
                    # config.py
                    unittestSuites = []
                    if branch_config['platforms'][platform].get('enable_opt_unittests'):
                        testTypes.append('opt')
                    if branch_config['platforms'][platform].get('enable_debug_unittests'):
                        testTypes.append('debug')

                    merge_tests = branch_config.get('enable_merging', True)

                    for test_type in testTypes:
                        test_builders = []
                        pgo_builders = []
                        triggeredUnittestBuilders = []
                        pgoUnittestBuilders = []
                        unittest_suites = "%s_unittest_suites" % test_type
                        if test_type == "debug":
                            # Debug tests always need to download symbols for
                            # runtime assertions
                            branch_config = deepcopy(branch_config)
                            pf = branch_config['platforms'][platform]
                            if pf.get('download_symbols', False) or pf.get('download_symbols_ondemand', True):
                                pf['download_symbols'] = True
                                pf['download_symbols_ondemand'] = False
                            slave_platform_name = "%s-debug" % slave_platform
                        elif test_type == "mobile":
                            slave_platform_name = "%s-mobile" % slave_platform
                        else:
                            slave_platform_name = slave_platform

                        # create builder names for TinderboxMailNotifier
                        for suites_name, suites in branch_config['platforms'][platform][slave_platform][unittest_suites]:
                            test_builders.extend(generateTestBuilderNames(
                                '%s %s %s test' % (platform_name, branch, test_type), suites_name, suites))
                            if create_pgo_builders and test_type == 'opt':
                                pgo_builders.extend(generateTestBuilderNames(
                                                    '%s %s pgo test' % (platform_name, branch), suites_name, suites))

                        triggeredUnittestBuilders.append(
                            (
                                'tests-%s-%s-%s-unittest' % (
                                    branch, slave_platform, test_type),
                                test_builders, merge_tests))
                        if create_pgo_builders and test_type == 'opt':
                            pgoUnittestBuilders.append(
                                (
                                    'tests-%s-%s-pgo-unittest' % (
                                        branch, slave_platform),
                                    pgo_builders, merge_tests))

                        for suites_name, suites in branch_config['platforms'][platform][slave_platform][unittest_suites]:
                            # create the builders
                            test_builder_kwargs = {
                                "config": branch_config,
                                "branch_name": branch,
                                "platform": platform,
                                "name_prefix": "%s %s %s test" % (platform_name, branch, test_type),
                                "build_dir_prefix": "%s_%s_test" % (branch, slave_platform_name),
                                "suites_name": suites_name,
                                "suites": suites,
                                "mochitestLeakThreshold": branch_config.get('mochitest_leak_threshold', None),
                                "crashtestLeakThreshold": branch_config.get('crashtest_leak_threshold', None),
                                "slaves": platform_config[slave_platform]['slaves'],
                                "resetHwClock": branch_config['platforms'][platform][slave_platform].get('reset_hw_clock', False),
                                "stagePlatform": stage_platform,
                                "stageProduct": stage_product
                            }
                            if isinstance(suites, dict) and "use_mozharness" in suites:
                                test_builder_kwargs['mozharness_repo'] = branch_config['mozharness_repo']
                                test_builder_kwargs['mozharness_tag'] = branch_config['mozharness_tag']
                                test_builder_kwargs['mozharness'] = True
                                test_builder_kwargs['mozharness_python'] = platform_config['mozharness_config']['mozharness_python']
                                if suites_name in branch_config['platforms'][platform][slave_platform].get('suite_config', {}):
                                    test_builder_kwargs['mozharness_suite_config'] = deepcopy(branch_config['platforms'][platform][slave_platform]['suite_config'][suites_name])
                                else:
                                    test_builder_kwargs[
                                        'mozharness_suite_config'] = {}
                                test_builder_kwargs['mozharness_suite_config']['hg_bin'] = platform_config['mozharness_config']['hg_bin']
                                test_builder_kwargs['mozharness_suite_config']['reboot_command'] = platform_config['mozharness_config']['reboot_command']
                                test_builder_kwargs['mozharness_suite_config']['env'] = MozillaEnvironments.get('%s-unittest' % platform, {}).copy()
                                test_builder_kwargs['mozharness_suite_config']['env'].update(branch_config['platforms'][platform].get('unittest-env', {}))
                                if suites.get('download_symbols', True) and branch_config['fetch_symbols'] and \
                                        branch_config['platforms'][platform][slave_platform].get('download_symbols', True):
                                    if test_type == 'opt':
                                        test_builder_kwargs['mozharness_suite_config']['download_symbols'] = 'ondemand'
                                    else:
                                        test_builder_kwargs['mozharness_suite_config']['download_symbols'] = 'true'
                            branchObjects['builders'].extend(
                                generateTestBuilder(**test_builder_kwargs))
                            if create_pgo_builders and test_type == 'opt':
                                pgo_builder_kwargs = test_builder_kwargs.copy()
                                pgo_builder_kwargs['name_prefix'] = "%s %s pgo test" % (platform_name, branch)
                                pgo_builder_kwargs[
                                    'build_dir_prefix'] += '_pgo'
                                pgo_builder_kwargs['stagePlatform'] += '-pgo'
                                branchObjects['builders'].extend(
                                    generateTestBuilder(**pgo_builder_kwargs))

                        for scheduler_name, test_builders, merge in triggeredUnittestBuilders:
                            for test in test_builders:
                                unittestSuites.append(test.split(' ')[-1])
                            scheduler_branch = ('%s-%s-%s-unittest' %
                                                (branch, platform, test_type))
                            if not merge:
                                nomergeBuilders.extend(test_builders)
                            extra_args = {}
                            if branch_config.get('enable_try'):
                                scheduler_class = BuilderChooserScheduler
                                extra_args['chooserFunc'] = tryChooser
                                extra_args['numberOfBuildsToTrigger'] = 1
                                extra_args['prettyNames'] = prettyNames
                                extra_args['unittestSuites'] = unittestSuites
                                extra_args['buildbotBranch'] = branch
                            else:
                                scheduler_class = Scheduler
                            branchObjects['schedulers'].append(scheduler_class(
                                name=scheduler_name,
                                branch=scheduler_branch,
                                builderNames=test_builders,
                                treeStableTimer=None,
                                **extra_args
                            ))
                        for scheduler_name, test_builders, merge in pgoUnittestBuilders:
                            for test in test_builders:
                                unittestSuites.append(test.split(' ')[-1])
                            scheduler_branch = '%s-%s-pgo-unittest' % (
                                branch, platform)
                            if not merge:
                                nomergeBuilders.extend(pgo_builders)
                            extra_args = {}
                            if branch_config.get('enable_try'):
                                scheduler_class = BuilderChooserScheduler
                                extra_args['chooserFunc'] = tryChooser
                                extra_args['numberOfBuildsToTrigger'] = 1
                                extra_args['prettyNames'] = prettyNames
                                extra_args['unittestSuites'] = unittestSuites
                                extra_args['buildbotBranch'] = branch
                            else:
                                scheduler_class = Scheduler
                            branchObjects['schedulers'].append(scheduler_class(
                                name=scheduler_name,
                                branch=scheduler_branch,
                                builderNames=pgo_builders,
                                treeStableTimer=None,
                                **extra_args
                            ))

            # Create one scheduler per # of tests to run
            for tests, builder_names in talos_builders.items():
                extra_args = {}
                if tests == 1:
                    scheduler_class = Scheduler
                    name = 'tests-%s-%s-talos' % (branch, platform)
                else:
                    scheduler_class = MultiScheduler
                    name = 'tests-%s-%s-talos-x%s' % (branch, platform, tests)
                    extra_args['numberOfBuildsToTrigger'] = tests

                if branch_config.get('enable_try'):
                    scheduler_class = BuilderChooserScheduler
                    extra_args['chooserFunc'] = tryChooser
                    extra_args['prettyNames'] = prettyNames
                    extra_args['talosSuites'] = SUITES.keys()
                    extra_args['numberOfBuildsToTrigger'] = tests
                    extra_args['buildbotBranch'] = branch

                s = scheduler_class(
                    name=name,
                    branch='%s-%s-talos' % (branch, platform),
                    treeStableTimer=None,
                    builderNames=builder_names,
                    **extra_args
                )
                branchObjects['schedulers'].append(s)
            # PGO Schedulers
            for tests, builder_names in talos_pgo_builders.items():
                extra_args = {}
                if tests == 1:
                    scheduler_class = Scheduler
                    name = 'tests-%s-%s-pgo-talos' % (branch, platform)
                else:
                    scheduler_class = MultiScheduler
                    name = 'tests-%s-%s-pgo-talos-x%s' % (
                        branch, platform, tests)
                    extra_args['numberOfBuildsToTrigger'] = tests

                if branch_config.get('enable_try'):
                    scheduler_class = BuilderChooserScheduler
                    extra_args['chooserFunc'] = tryChooser
                    extra_args['prettyNames'] = prettyNames
                    extra_args['talosSuites'] = SUITES.keys()
                    extra_args['numberOfBuildsToTrigger'] = tests
                    extra_args['buildbotBranch'] = branch

                s = scheduler_class(
                    name=name,
                    branch='%s-%s-pgo-talos' % (branch, platform),
                    treeStableTimer=None,
                    builderNames=builder_names,
                    **extra_args
                )
                branchObjects['schedulers'].append(s)

    if branch_config.get('release_tests'):
        releaseObjects = generateTalosReleaseBranchObjects(branch,
                                                           branch_config, PLATFORMS, SUITES, ACTIVE_UNITTEST_PLATFORMS, factory_class)
        for k, v in releaseObjects.items():
            branchObjects[k].extend(v)
    return branchObjects


def generateTalosReleaseBranchObjects(branch, branch_config, PLATFORMS, SUITES,
                                      ACTIVE_UNITTEST_PLATFORMS, factory_class=TalosFactory):
    branch_config = branch_config.copy()
    release_tests = branch_config['release_tests']

    # Update the # of tests to run with our release_tests number
    # Force no merging
    for suite, talosConfig in SUITES.items():
        tests, merge, extra, platforms = branch_config['%s_tests' % suite]
        if tests > 0:
            branch_config['%s_tests' % suite] = (release_tests,
                                                 False, extra, platforms)

    # Update the TinderboxTree and the branch_name
    branch_config['tinderbox_tree'] += '-Release'
    branch_config['branch_name'] += '-Release'
    branch = "release-" + branch

    # Remove the release_tests key so we don't call ourselves again
    del branch_config['release_tests']

    # Don't fetch symbols
    branch_config['fetch_symbols'] = branch_config['fetch_release_symbols']
    return generateTalosBranchObjects(branch, branch_config, PLATFORMS, SUITES,
                                      ACTIVE_UNITTEST_PLATFORMS, factory_class)


def mirrorAndBundleArgs(config):
    args = []
    mirrors = None
    if config['base_mirror_urls']:
        mirrors = ["%s/%s" % (url, config['repo_path'])
                   for url in config['base_mirror_urls']]
    if mirrors:
        for mirror in mirrors:
            args.extend(["--mirror", mirror])
    bundles = None
    if config['base_bundle_urls']:
        bundles = ["%s/%s.hg" % (url, config['repo_path'].rstrip(
            '/').split('/')[-1]) for url in config['base_bundle_urls']]
    if bundles:
        for bundle in bundles:
            args.extend(["--bundle", bundle])
    return args


def generateBlocklistBuilder(config, branch_name, platform, base_name, slaves):
    pf = config['platforms'].get(platform, {})
    extra_args = ['-b', config['repo_path'],
                  '--hgtool', 'scripts/buildfarm/utils/hgtool.py']

    extra_args += mirrorAndBundleArgs(config)
    if pf['product_name'] is not None:
        extra_args.extend(['-p', pf['product_name']])
    if config['hg_username'] is not None:
        extra_args.extend(['-u', config['hg_username']])
    if config['hg_ssh_key'] is not None:
        extra_args.extend(['-k', config['hg_ssh_key']])
    if config['blocklist_update_on_closed_tree'] is True:
        extra_args.extend(['-c'])
    if config['blocklist_update_set_approval'] is True:
        extra_args.extend(['-a'])
    blocklistupdate_factory = ScriptFactory(
        "%s%s" % (config['hgurl'],
        config['build_tools_repo_path']),
        'scripts/blocklist/sync-hg-blocklist.sh',
        interpreter='bash',
        extra_args=extra_args,
    )
    blocklistupdate_builder = {
        'name': '%s blocklist update' % base_name,
        'slavenames': slaves,
        'builddir': '%s-%s-blocklistupdate' % (branch_name, platform),
        'slavebuilddir': normalizeName('%s-%s-blocklistupdate' % (branch_name, platform)),
        'factory': blocklistupdate_factory,
        'category': branch_name,
        'properties': {'branch': branch_name, 'platform': platform, 'slavebuilddir': normalizeName('%s-%s-blocklistupdate' % (branch_name, platform)), 'product': 'firefox'},
    }
    return blocklistupdate_builder


def generateHSTSBuilder(config, branch_name, platform, base_name, slaves):
    pf = config['platforms'].get(platform, {})
    extra_args = ['-b', config['repo_path'],
                  '--hgtool', 'scripts/buildfarm/utils/hgtool.py']

    extra_args += mirrorAndBundleArgs(config)
    if pf['product_name'] is not None:
        extra_args.extend(['-p', pf['product_name']])
    if config['hg_username'] is not None:
        extra_args.extend(['-u', config['hg_username']])
    if config['hg_ssh_key'] is not None:
        extra_args.extend(['-k', config['hg_ssh_key']])
    if config['hsts_update_on_closed_tree'] is True:
        extra_args.extend(['-c'])
    if config['hsts_update_set_approval'] is True:
        extra_args.extend(['-a'])
    hsts_update_factory = ScriptFactory(
        "%s%s" % (config['hgurl'],
        config['build_tools_repo_path']),
        'scripts/hsts/update_hsts_preload_list.sh',
        interpreter='bash',
        extra_args=extra_args,
    )
    hsts_update_builder = {
        'name': '%s hsts preload update' % base_name,
        'slavenames': slaves,
        'builddir': '%s-%s-hsts' % (branch_name, platform),
        'slavebuilddir': normalizeName('%s-%s-hsts' % (branch_name, platform)),
        'factory': hsts_update_factory,
        'category': branch_name,
        'properties': {'branch': branch_name, 'platform': platform, 'slavebuilddir': normalizeName('%s-%s-hsts' % (branch_name, platform)), 'product': 'firefox'},
    }
    return hsts_update_builder


def generateFuzzingObjects(config, SLAVES):
    builders = []
    f = ScriptFactory(
        config['scripts_repo'],
        'scripts/fuzzing/fuzzer.sh',
        interpreter='bash',
        script_timeout=1800,
        script_maxtime=2100,
        reboot_command=['python',
                        'scripts/buildfarm/maintenance/count_and_reboot.py',
                        '-f', './reboot_count.txt',
                        '-n', '0',
                        '-z'],
    )
    for platform in config['platforms']:
        env = MozillaEnvironments.get("%s-unittest" % platform, {}).copy()
        env['HG_BUNDLE'] = config['fuzzing_bundle']
        env['HG_REPO'] = config['fuzzing_repo']
        env['FUZZ_REMOTE_HOST'] = config['fuzzing_remote_host']
        env['FUZZ_BASE_DIR'] = config['fuzzing_base_dir']
        if 'win' in platform:
            env['PATH'] = "${MOZILLABUILD}buildbotve\\scripts;${PATH}"
        builder = {'name': 'fuzzer-%s' % platform,
                   'builddir': 'fuzzer-%s' % platform,
                   'slavenames': SLAVES[platform],
                   'nextSlave': _nextSlowIdleSlave(config['idle_slaves']),
                   'factory': f,
                   'category': 'idle',
                   'env': env,
                   'properties': {
                       'branch': 'idle',
                       'platform': platform,
                       'product': 'fuzzing',
                   },
                   }
        builders.append(builder)
        nomergeBuilders.append(builder)
    fuzzing_scheduler = PersistentScheduler(
        name="fuzzer",
        builderNames=[b['name'] for b in builders],
        numPending=2,
        pollInterval=300,  # Check every 5 minutes
    )
    return {
        'builders': builders,
        'schedulers': [fuzzing_scheduler],
    }


def generateNanojitObjects(config, SLAVES):
    builders = []
    branch = os.path.basename(config['repo_path'])

    for platform in config['platforms']:
        if 'win' in platform:
            slaves = SLAVES[platform]
            nanojit_script = 'scripts/nanojit/nanojit.sh'
            interpreter = 'bash'
        else:
            slaves = SLAVES[platform]
            nanojit_script = 'scripts/nanojit/nanojit.sh'
            interpreter = None

        f = ScriptFactory(
            config['scripts_repo'],
            nanojit_script,
            interpreter=interpreter,
            log_eval_func=rc_eval_func({1: WARNINGS}),
        )

        builder = {'name': 'nanojit-%s' % platform,
                   'builddir': 'nanojit-%s' % platform,
                   'slavenames': slaves,
                   'nextSlave': _nextSlowIdleSlave(config['idle_slaves']),
                   'factory': f,
                   'category': 'idle',
                   'properties': {'branch': branch, 'platform': platform, 'product': 'nanojit'},
                   }
        builders.append(builder)
        nomergeBuilders.append(builder)

    # Set up polling
    poller = HgPoller(
        hgURL=config['hgurl'],
        branch=config['repo_path'],
        pollInterval=5 * 60,
    )

    # Set up scheduler
    scheduler = Scheduler(
        name="nanojit",
        branch=config['repo_path'],
        treeStableTimer=None,
        builderNames=[b['name'] for b in builders],
    )

    return {
        'builders': builders,
        'change_source': [poller],
        'schedulers': [scheduler],
        'status': [],
    }


def generateSpiderMonkeyObjects(project, config, SLAVES):
    builders = []
    branch = config['branch']
    assert branch == os.path.basename(config['repo_path']), "spidermonkey project object has mismatched branch and repo_path"
    bconfig = config['branchconfig']

    PRETTY_NAME = '%s %s-%s build'
    prettyNames = {}
    for platform, variants in config['variants'].items():
        interpreter = None
        if 'win' in platform:
            interpreter = 'bash'

        pf = config['platforms'][platform]
        env = pf['env'].copy()
        env['HG_REPO'] = config['hgurl'] + config['repo_path']

        for variant in variants:
            factory_platform_args = ['use_mock',
                                     'mock_target',
                                     'mock_packages',
                                     'mock_copyin_files']
            factory_kwargs = {}
            for a in factory_platform_args:
                if a in pf:
                    factory_kwargs[a] = pf[a]
            factory_kwargs['env'] = env

            extra_args = ['-r', WithProperties("%(revision)s")]
            extra_args += mirrorAndBundleArgs(bconfig)
            extra_args += [variant]

            f = ScriptFactory(
                config['scripts_repo'],
                'scripts/spidermonkey_builds/spidermonkey.sh',
                interpreter=interpreter,
                log_eval_func=rc_eval_func({1: WARNINGS}),
                extra_args=tuple(extra_args),
                script_timeout=3600,
                **factory_kwargs
            )

            # Fill in interpolated variables in pf['base_name'], which is currently only
            # "%(branch)s"
            base_name = pf['base_name'] % config

            prettyName = PRETTY_NAME % (base_name, project, variant)
            name = prettyName
            if not config.get('try_by_default', True):
                prettyName += ' try-nondefault'
            prettyNames[platform] = prettyName

            builder = {'name': name,
                       'builddir': '%s_%s_spidermonkey-%s' % (branch, platform, variant),
                       'slavebuilddir': normalizeName('%s_%s_spidermonkey-%s' % (branch, platform, variant)),
                       'slavenames': pf['slaves'],
                       'nextSlave': _nextSlowIdleSlave(config['idle_slaves']),
                       'factory': f,
                       'category': branch,
                       'env': env,
                       'properties': {'branch': branch, 'platform': platform, 'product': 'spidermonkey'},
                       }
            builders.append(builder)
            if not bconfig.get('enable_merging', True):
                nomergeBuilders.append(name)

    def isImportant(change):
        if not shouldBuild(change):
            return False

        for f in change.files:
            if f.startswith("js/src"):
                return True
        return False

    # Set up scheduler
    extra_args = {}
    scheduler_class = None
    if config.get('enable_try'):
        scheduler_class = makePropertiesScheduler(
            BuilderChooserScheduler, [buildUIDSchedFunc])
        extra_args['chooserFunc'] = tryChooser
        extra_args['numberOfBuildsToTrigger'] = 1
        extra_args['prettyNames'] = prettyNames
        extra_args['buildbotBranch'] = branch
    else:
        scheduler_class = Scheduler

    scheduler = scheduler_class(
        name=project,
        treeStableTimer=None,
        builderNames=[b['name'] for b in builders],
        fileIsImportant=isImportant,
        change_filter=ChangeFilter(
            branch=config['repo_path'], filter_fn=isImportant),
        **extra_args
    )

    return {
        'builders': builders,
        'schedulers': [scheduler],
    }


def generateJetpackObjects(config, SLAVES):
    builders = []
    project_branch = os.path.basename(config['repo_path'])
    for branch in config['branches']:
        for platform in config['platforms'].keys():
            slaves = SLAVES[platform]
            jetpackTarball = "%s/%s/%s" % (config['hgurl'], config['repo_path'], config['jetpack_tarball'])
            ftp_url = config['ftp_url']
            types = ['opt', 'debug']
            for type in types:
                if type == 'debug':
                    ftp_url = ftp_url + "-debug"
                f = ScriptFactory(
                    config['scripts_repo'],
                    'buildfarm/utils/run_jetpack.py',
                    extra_args=(
                    "-p", platform, "-t", jetpackTarball, "-b", branch,
                        "-f", ftp_url, "-e", config['platforms'][platform]['ext'],),
                    interpreter='python',
                    log_eval_func=rc_eval_func({1: WARNINGS, 2: FAILURE,
                                                4: EXCEPTION, 5: RETRY}),
                    reboot_command=['python',
                                    'scripts/buildfarm/maintenance/count_and_reboot.py',
                                    '-f', './reboot_count.txt',
                                    '-n', '0',
                                    '-z'],
                )

                builder = {'name': 'jetpack-%s-%s-%s' % (branch, platform, type),
                           'builddir': 'jetpack-%s-%s-%s' % (branch, platform, type),
                           'slavebuilddir': 'test',
                           'slavenames': slaves,
                           'factory': f,
                           'category': 'jetpack',
                           'properties': {'branch': project_branch, 'platform': platform, 'product': 'jetpack'},
                           'env': MozillaEnvironments.get("%s" % config['platforms'][platform].get('env'), {}).copy(),
                           }
                builders.append(builder)
                nomergeBuilders.append(builder)

    # Set up polling
    poller = HgPoller(
        hgURL=config['hgurl'],
        branch=config['repo_path'],
        pollInterval=5 * 60,
    )

    # Set up scheduler
    scheduler = Scheduler(
        name="jetpack",
        branch=config['repo_path'],
        treeStableTimer=None,
        builderNames=[b['name'] for b in builders],
    )

    return {
        'builders': builders,
        'change_source': [poller],
        'schedulers': [scheduler],
    }


def generateDXRObjects(config, SLAVES):
    builders = []
    branch = os.path.basename(config['repo_path'])

    platform = config['platform']
    slaves = SLAVES[platform]
    script = 'scripts/dxr/dxr.sh'

    f = ScriptFactory(
        config['scripts_repo'],
        script,
        log_eval_func=rc_eval_func({1: WARNINGS}),
        script_timeout=7200,
    )

    builder = {'name': 'dxr-%s' % branch,
               'env': config['env'],
               'builddir': 'dxr-%s' % branch,
               'slavenames': slaves,
               'factory': f,
               'category': 'idle',
               'properties': {
                   'branch': branch,
                   'platform': platform,
                   'product': 'dxr',
                   'upload_host': config['upload_host'],
                   'upload_user': config['upload_user'],
                   'upload_sshkey': config['upload_sshkey'],
               },
               }
    builders.append(builder)

    # Set up scheduler
    scheduler = Nightly(
        name="dxr-%s" % branch,
        branch=config['repo_path'],
        hour=[3], minute=[05],
        builderNames=[b['name'] for b in builders],
    )

    return {
        'builders': builders,
        'schedulers': [scheduler],
    }


def generateProjectObjects(project, config, SLAVES):
    builders = []
    schedulers = []
    change_sources = []
    status = []
    buildObjects = {
        'builders': builders,
        'schedulers': schedulers,
        'status': status,
        'change_source': change_sources,
    }

    # Fuzzing
    if project.startswith('fuzzing'):
        fuzzingObjects = generateFuzzingObjects(config, SLAVES)
        buildObjects = mergeBuildObjects(buildObjects, fuzzingObjects)

    # Nanojit
    elif project == 'nanojit':
        nanojitObjects = generateNanojitObjects(config, SLAVES)
        buildObjects = mergeBuildObjects(buildObjects, nanojitObjects)

    # Jetpack
    elif project.startswith('jetpack'):
        jetpackObjects = generateJetpackObjects(config, SLAVES)
        buildObjects = mergeBuildObjects(buildObjects, jetpackObjects)

    # Spidermonkey
    elif project.startswith('spidermonkey'):
        spiderMonkeyObjects = generateSpiderMonkeyObjects(
            project, config, SLAVES)
        buildObjects = mergeBuildObjects(buildObjects, spiderMonkeyObjects)

    # DXR
    elif project.startswith('dxr'):
        dxrObjects = generateDXRObjects(config, SLAVES)
        buildObjects = mergeBuildObjects(buildObjects, dxrObjects)

    return buildObjects


def makeLogUploadCommand(branch_name, config, is_try=False, is_shadow=False,
                         platform_prop="platform", product_prop=None, product=None):
    extra_args = []
    if config.get('enable_mail_notifier'):
        if config.get('notify_real_author'):
            extraRecipients = []
            sendToAuthor = True
        else:
            extraRecipients = config['email_override']
            sendToAuthor = False

        upload_cmd = 'try_mailer.py'
        extra_args.extend(['-f', 'tryserver@build.mozilla.org'])
        for r in extraRecipients:
            extra_args.extend(['-t', r])
        if sendToAuthor:
            extra_args.append("--to-author")
    else:
        upload_cmd = 'log_uploader.py'

    logUploadCmd = [sys.executable,
                    '%s/bin/%s' % (buildbotcustom.__path__[0], upload_cmd),
                    config['stage_server'],
                    '-u', config['stage_username'],
                    '-i', os.path.expanduser(
                        "~/.ssh/%s" % config['stage_ssh_key']),
                    '-b', branch_name,
                    ]

    if platform_prop:
        logUploadCmd += ['-p', WithProperties("%%(%s)s" % platform_prop)]
    logUploadCmd += extra_args

    if product_prop:
        logUploadCmd += ['--product', WithProperties("%%(%s)s" % product_prop)]
        assert not product, 'dont specify static value when using property'
    elif product:
        logUploadCmd.extend(['--product', product])

    if is_try:
        logUploadCmd.append('--try')

    if is_shadow:
        logUploadCmd.append('--shadow')

    return logUploadCmd
