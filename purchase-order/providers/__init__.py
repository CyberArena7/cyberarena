from . import common
from . import spainsellers, kaquucomponentes, coolaccesorios, soluziondigital, pcxeon, pccomponentes

class ProviderNotDetected(Exception):
    pass

def parse(data: str) -> common.Invoice:
    if spainsellers.detect(data):
        invoice = spainsellers.parse(data)
    elif kaquucomponentes.detect(data):
        invoice = kaquucomponentes.parse(data)
    elif coolaccesorios.detect(data):
        invoice = coolaccesorios.parse(data)
    elif soluziondigital.detect(data):
        invoice = soluziondigital.parse(data)
    elif pcxeon.detect(data):
        invoice = pcxeon.parse(data)
    else:
        raise ProviderNotDetected

    #assert(common.check_total_price(invoice))
    return invoice 