from zope.interface import implements

from buildbot import interfaces

class MercurialEmailLookup:
    implements(interfaces.IEmailLookup)

    def getAddress(self, user):
        return user
