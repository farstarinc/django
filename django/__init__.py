VERSION = (1, 3, 0, 'final', 0)
VERSION = VERSION + ('farstar', 1)

def get_version():
    version = '%s.%s' % (VERSION[0], VERSION[1])
    if VERSION[2]:
        version = '%s.%s' % (version, VERSION[2])
    else:
        if VERSION[3] != 'final':
            version = '%s %s%s' % (version, VERSION[3], (VERSION[4] if VERSION[4] > 0 else ''))

    version = "%s %s%s" % (version, VERSION[5], (VERSION[6] if VERSION[6] > 0 else ''))
    return version
