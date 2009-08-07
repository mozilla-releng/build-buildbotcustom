#!/usr/bin/env python
"""
Drops all the tables, and re-creates them
"""
from buildbotcustom.status.db.model import connect
import sys
connect(sys.argv[1], drop_all=True)
