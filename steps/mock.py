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
# ***** END LICENSE BLOCK *****

from pipes import quote
from buildbot.status.builder import STDOUT, STDERR
from buildbot.steps.shell import WithProperties
from buildbotcustom.steps.base import addRetryEvaluateCommand
from buildbotcustom.steps.base import ShellCommand
import buildbotcustom.steps.unittest as unittest_steps
import buildbotcustom.steps.test


class MockCommand(ShellCommand):
    # Note: this class doesn't deal with all WithProperties invocations.
    # in particular, it only deals with the WithProperties('format string')
    # case
    # Things to address:
    #   -what happens if the workdir doesn't exist?
    #     -only issue if this is the first command called in a build
    #   -should add reconfig/checkconfig time check for valid workdir
    #   -doesn't currently set properties
    #   -set_mock_command function should be implemented as a mixin or something

    def __init__(self, mock=False, mock_login='mock_mozilla', target=None,
                 mock_workdir_mutator=lambda x: x, mock_args=['--unpriv'],
                 mock_workdir_prefix='%(basedir)s/',
                 **kwargs):
        self.super_class = ShellCommand
        self.super_class.__init__(self, **kwargs)
        self.mock = mock
        self.mock_login = mock_login
        self.mock_args = mock_args
        self.mock_workdir_prefix = mock_workdir_prefix
        self.mock_workdir_mutator = mock_workdir_mutator
        self.target = target
        assert 'workdir' in kwargs.keys(), "You *must* specify workdir"
        self.addFactoryArguments(
            mock=mock,
            mock_login=mock_login,
            mock_workdir_mutator=mock_workdir_mutator,
            mock_args=mock_args,
            mock_workdir_prefix=mock_workdir_prefix,
            target=target,
        )

    def set_mock_command(self):
        # This variable is used to decide whether to wrap the

        # We need to have all commands as a string.  We'll
        # convert argv commands into string commands
        if isinstance(self.command, list):
            string_list = []
            for arg in self.command:
                if issubclass(arg.__class__, WithProperties):
                    string_list.append(quote(str(arg.fmtstring)))
                else:
                    string_list.append(quote(str(arg)))
            string_command = ' '.join(string_list)
        elif issubclass(self.command.__class__, WithProperties):
            string_command = self.command.fmtstring
        else:
            string_command = self.command
        mock_workdir = self.mock_workdir_mutator(self.remote_kwargs['workdir'])

        # If the workdir is a WithProperties instance, we need to get the format
        # string and wrap it in another WithProperties
        if issubclass(mock_workdir.__class__, WithProperties):
            mock_workdir = mock_workdir.fmtstring
        if self.mock_workdir_prefix is not None:
            mock_workdir = self.mock_workdir_prefix + mock_workdir

        if 'env' in self.remote_kwargs:
            pre_render_env = self.remote_kwargs['env']
            properties = self.build.getProperties()
            rendered_env = properties.render(pre_render_env)
            environment = ' '.join('%s="%s"' % (k, rendered_env[k])
                                   for k in rendered_env.keys())
        else:
            environment = ''

        self.command = [self.mock_login, '-r', self.target,
                        '--cwd', WithProperties(mock_workdir)] + \
            self.mock_args + ['--shell'] + \
            [WithProperties('/usr/bin/env %s %s' % (environment,
                                                    string_command))]

    def start(self):
        if self.mock:
            self.set_mock_command()
        self.super_class.start(self)


class MockProperty(MockCommand):
    # This class could be implemented cleaner by implementing
    # the mock logic differently. Patches accepted
    name = "mock-setproperty"

    def __init__(self, property=None, extract_fn=None, strip=True, **kwargs):
        self.property = property
        self.extract_fn = extract_fn
        self.strip = strip

        assert (property is not None) ^ (extract_fn is not None), \
            "Exactly one of property and extract_fn must be set"

        self.super_class = MockCommand
        self.super_class.__init__(self, **kwargs)

        self.addFactoryArguments(
            property=self.property,
            extract_fn=self.extract_fn,
            strip=self.strip)

        self.property_changes = {}

    def commandComplete(self, cmd):
        if self.property:
            result = cmd.logs['stdio'].getText()
            if self.strip:
                result = result.strip()
            propname = self.build.getProperties().render(self.property)
            self.setProperty(propname, result, "MockProperty Step")
            self.property_changes[propname] = result
        else:
            log = cmd.logs['stdio']
            new_props = self.extract_fn(cmd.rc,
                                        ''.join(log.getChunks(
                                                [STDOUT], onlyText=True)),
                                        ''.join(log.getChunks([STDERR], onlyText=True)))
            for k, v in new_props.items():
                self.setProperty(k, v, "MockProperty Step")
            self.property_changes = new_props

    def createSummary(self, log):
        props_set = ["%s: %r" % (k, v)
                     for k, v in self.property_changes.items()]
        self.addCompleteLog('property changes', "\n".join(props_set))

    def getText(self, cmd, results):
        if self.property_changes:
            return ["set props:"] + self.property_changes.keys()
        else:
            return ["no change"]


class MockReset(ShellCommand):
    haltOnFailure = True
    name = "mock-reset"

    def __init__(self, target, **kwargs):
        kwargs['command'] = "sh -c " \
            "'rm -f /builds/mock_mozilla/%s/buildroot.lock; " \
            "mock_mozilla -r %s --orphanskill'" % (target, target)
        assert target is not None, "target is required"
        self.super_class = ShellCommand
        self.super_class.__init__(self, **kwargs)
        self.target = target
        self.description = ['mock-tgt', target]
        self.addFactoryArguments(target=target)


class MockInit(ShellCommand):
    haltOnFailure = True
    name = "mock-init"

    def __init__(self, target, **kwargs):
        kwargs['command'] = "mock_mozilla -r %s --init" % target
        self.super_class = ShellCommand
        self.super_class.__init__(self, **kwargs)
        self.target = target
        self.description = ['mock-tgt', target]
        self.addFactoryArguments(target=target)


class MockInstall(ShellCommand):
    haltOnFailure = True
    name = "mock-install"

    def __init__(self, target, packages, **kwargs):
        if packages is None:
            packages = []
        kwargs['command'] = "mock_mozilla -r %s --install %s" % \
            (target, ' '.join(packages))
        self.super_class = ShellCommand
        self.super_class.__init__(self, **kwargs)
        self.target = target
        self.description = ['mock-install'] + packages
        self.addFactoryArguments(target=target, packages=packages)


def addMockCommand(obj):
    # Note: changes to MockCommand should be ported here as well
    class C(obj):
        def __init__(self, mock=False, mock_login='mock_mozilla',
                     mock_workdir_mutator=lambda x: x, target=None,
                     mock_args=['--unpriv'],
                     mock_workdir_prefix='%(basedir)s/', **kwargs):
            self.super_class = obj
            self.mock = mock
            self.timeout = kwargs.get('timeout', 1200)
            self.mock_login = kwargs.get('mock_login', 'mock_mozilla')
            self.target = target
            self.mock_args = mock_args
            self.mock_workdir_prefix = mock_workdir_prefix
            self.mock_workdir_mutator = kwargs.get('mock_workdir_mutator',
                                                   lambda x: x)
            assert 'workdir' in kwargs.keys(), "You *must* specify workdir"

            self.super_class.__init__(self, **kwargs)

            self.properties_rendered = False

            self.addFactoryArguments(
                command=self.command,
                mock=mock,
                mock_login=mock_login,
                target=self.target,
                mock_workdir_mutator=mock_workdir_mutator,
                mock_args=mock_args,
                mock_workdir_prefix=mock_workdir_prefix,
            )

        def set_mock_command(self):
            if self.properties_rendered:
                return
            self.properties_rendered = True

            # We need to have all commands as a string.  We'll
            # convert argv commands into string commands
            if isinstance(self.command, list):
                string_list = []
                for arg in self.command:
                    if issubclass(arg.__class__, WithProperties):
                        string_list.append(quote(str(arg.fmtstring)))
                    else:
                        string_list.append(quote(str(arg)))
                string_command = ' '.join(string_list)
            elif issubclass(self.command.__class__, WithProperties):
                string_command = self.command.fmtstring
            else:
                string_command = self.command
            mock_workdir = self.mock_workdir_mutator(
                self.remote_kwargs['workdir'])

            # If the workdir is a WithProperties instance, we need to get the
            # format string and wrap it in another WithProperties
            if issubclass(mock_workdir.__class__, WithProperties):
                mock_workdir = mock_workdir.fmtstring
            if self.mock_workdir_prefix is not None:
                mock_workdir = self.mock_workdir_prefix + mock_workdir

            if 'env' in self.remote_kwargs:
                pre_render_env = self.remote_kwargs['env']
                properties = self.build.getProperties()
                rendered_env = properties.render(pre_render_env)
                environment = ' '.join('%s="%s"' % (k, rendered_env[k])
                                       for k in rendered_env.keys())
            else:
                environment = ''

            self.command = [self.mock_login, '-r', self.target,
                            '--cwd', WithProperties(mock_workdir)] + \
                self.mock_args + ['--shell'] + \
                [WithProperties('/usr/bin/env %s %s' % (environment,
                                                        string_command))]

        def start(self):
            if self.mock:
                self.set_mock_command()
            self.super_class.start(self)

    return C

MockMozillaCheck = addMockCommand(unittest_steps.MozillaCheck)
RetryingMockCommand = addRetryEvaluateCommand(MockCommand)
RetryingMockProperty = addRetryEvaluateCommand(MockProperty)
