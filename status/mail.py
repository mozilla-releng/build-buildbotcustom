from email.message import Message
from email.utils import formatdate

from zope.interface import implements
from twisted.internet import defer
from twisted.mail.smtp import sendmail
from twisted.python import log as twlog

from buildbot import interfaces
from buildbot.status import base
from buildbot.status import mail

ENCODING = 'utf8'

class MercurialEmailLookup:
    implements(interfaces.IEmailLookup)

    def getAddress(self, user):
        return user

def defaultChangeMessage(change):
    revision = change.revision
    msgdict = {"type": "plain"}
    msgdict["body"] = "Thanks for your submission (%s)."
    return msgdict

class ChangeNotifier(base.StatusReceiverMultiService):
    compare_attrs = ('fromaddr', 'categories', 'branches', 'subject',
            'relayhost', 'lookup', 'extraRecipients', 'sendToInterestedUsers',
            'messageFormatter', 'extraHeaders', 'smtpUser', 'smtpPassword',
            'smtpPort')

    def __init__(self, fromaddr, categories=None, branches=None,
            subject="Notifcation of change %(revision)s on branch %(branch)s",
            relayhost="localhost", lookup=None, extraRecipients=None,
            sendToInterestedUsers=True, messageFormatter=defaultChangeMessage,
            extraHeaders=None, smtpUser=None, smtpPassword=None, smtpPort=25):

        base.StatusReceiverMultiService.__init__(self)

        self.fromaddr = fromaddr
        self.categories = categories
        self.branches = branches
        self.relayhost = relayhost
        if lookup is not None:
            if type(lookup) is str:
                lookup = mail.Domain(lookup)
            assert interfaces.IEmailLookup.providedBy(lookup)
        self.lookup = lookup
        self.smtpUser = smtpUser
        self.smtpPassword = smtpPassword
        self.smtpPort = smtpPort
        self.master_status = None
        self.subject = subject
        if extraHeaders:
            assert isinstance(extraHeaders, dict)
        self.extraHeaders = extraHeaders
        self.messageFormatter = messageFormatter
        self.sendToInterestedUsers = sendToInterestedUsers
        if extraRecipients:
            assert isinstance(extraRecipients, (list, tuple))
            for r in extraRecipients:
                assert isinstance(r, str)
                assert mail.VALID_EMAIL.search(r) # require full email addresses, not User names
            self.extraRecipients = extraRecipients
        else:
            self.extraRecipients = []

        # you should either limit on branches or categories, not both
        assert not (self.branches != None and self.categories != None)

    def setServiceParent(self, parent):
        """
        @type  parent: L{buildbot.master.BuildMaster}
        """
        base.StatusReceiverMultiService.setServiceParent(self, parent)
        self.setup()

    def setup(self):
        self.master_status = self.parent.getStatus()
        self.master_status.subscribe(self)

    def disownServiceParent(self):
        self.master_status.unsubscribe(self)
        return base.StatusReceiverMultiService.disownServiceParent(self)

    def changeAdded(self, change):
        if self.branches and change.branch not in self.branches:
            return

        if self.categories and change.category not in self.categories:
            return

        return self.buildMessage(change)

    def createEmail(self, msgdict, change):
        text = msgdict['body'].encode(ENCODING)
        type_ = msgdict['type']
        if 'subject' in msgdict:
            subject = msgdict['subject'].encode(ENCODING)
        else:
            subject = self.subject % change.asDict()

        assert type_ in ('plain', 'html'), "'%s' message type must be 'plain' or 'html'." % type_

        m = Message()
        m.set_payload(text, ENCODING)
        m.set_type("text/%s" % type_)

        m['Date'] = formatdate(localtime=True)
        m['Subject'] = subject
        m['From'] = self.fromaddr
        # m['To'] is added later

        # Add any extra headers that were requested, doing
        # interpolation if necessary
        if self.extraHeaders:
            d = change.asDict()
            for k,v in self.extraHeaders.items():
                k = k % d
                if k in m:
                    twlog.msg("Warning: Got header " + k + " in self.extraHeaders "
                          "but it already exists in the Message - "
                          "not adding it.")
                    continue
                m[k] = v % d

        if 'headers' in msgdict:
            d = change.asDict()
            for k,v in msgdict['headers'].items():
                k = k % d
                if k in m:
                    twlog.msg("Warning: Got header " + k + " in self.extraHeaders "
                          "but it already exists in the Message - "
                          "not adding it.")
                    continue
                m[k] = v % d

        return m

    def buildMessage(self, change):
        msgdict = self.messageFormatter(change)

        m = self.createEmail(msgdict, change)

        # now, who is this message going to?
        dl = []
        recipients = []
        if self.sendToInterestedUsers and self.lookup:
            d = defer.maybeDeferred(self.lookup.getAddress, change.who)
            d.addCallback(recipients.append)
            dl.append(d)
        d = defer.DeferredList(dl)
        d.addCallback(self._gotRecipients, recipients, m)
        return d

    def _gotRecipients(self, res, rlist, m):
        recipients = set()

        for r in rlist:
            if r is None: # getAddress didn't like this address
                continue

            # Git can give emails like 'User' <user@foo.com>@foo.com so check
            # for two @ and chop the last
            if r.count('@') > 1:
                r = r[:r.rindex('@')]

            if mail.VALID_EMAIL.search(r):
                recipients.add(r)
            else:
                twlog.msg("INVALID EMAIL: %r" + r)

        # if we're sending to interested users move the extra's to the CC
        # list so they can tell if they are also interested in the change
        # unless there are no interested users
        if self.sendToInterestedUsers and len(recipients):
            extra_recips = self.extraRecipients[:]
            extra_recips.sort()
            m['CC'] = ", ".join(extra_recips)
        else:
            [recipients.add(r) for r in self.extraRecipients[:]]

        rlist = list(recipients)
        rlist.sort()
        m['To'] = ", ".join(rlist)

        # The extras weren't part of the TO list so add them now
        if self.sendToInterestedUsers:
            for r in self.extraRecipients:
                recipients.add(r)

        return self.sendMessage(m, list(recipients))

    def sendMessage(self, m, recipients):
        s = m.as_string()
        twlog.msg("sending mail (%d bytes) to" % len(s), recipients)
        return sendmail(self.relayhost, self.fromaddr, recipients, s,
                        port=self.smtpPort)
