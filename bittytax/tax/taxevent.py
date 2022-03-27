from decimal import Decimal

from ..config import config


class TaxEvent(object):
    def __init__(self, date, asset, acq_date=None):
        self.date = date
        self.asset = asset
        self.acq_date = acq_date

    def __eq__(self, other):
        return self.date == other.date

    def __ne__(self, other):
        return not self == other

    def __lt__(self, other):
        return self.date < other.date


class TaxEventCapitalGains(TaxEvent):
    def __init__(self, disposal_type, b, s, cost, fees):
        super(TaxEventCapitalGains, self).__init__(s.timestamp, s.asset, acq_date=b.timestamp)
        self.disposal_type = disposal_type
        self.quantity = s.quantity
        self.cost = cost.quantize(config.PRECISION)
        self.fees = fees.quantize(config.PRECISION)
        self.proceeds = s.proceeds.quantize(config.PRECISION)
        self.gain = self.proceeds - self.cost - self.fees
        self.acquisition_date = b.timestamp if b else None

    def format_disposal(self):
        # TODO FIXME
        # if self.disposal_type == TaxCalculator.DISPOSAL_BED_AND_BREAKFAST:
        #    return "%s (%s)" % (self.disposal_type, self.acquisition_date.strftime(config.DATE_FORMAT))

        return self.disposal_type

    def __str__(self):
        return "Disposal(%s) gain=%s (proceeds=%s - cost=%s - fees=%s)" % (
            self.disposal_type.lower(),
            config.sym() + '{:0,.2f}'.format(self.gain),
            config.sym() + '{:0,.2f}'.format(self.proceeds),
            config.sym() + '{:0,.2f}'.format(self.cost),
            config.sym() + '{:0,.2f}'.format(self.fees))


class TaxEventIncome(TaxEvent):
    def __init__(self, b):
        super(TaxEventIncome, self).__init__(b.timestamp, b.asset)
        self.type = b.t_type
        self.quantity = b.quantity
        self.amount = b.cost.quantize(config.PRECISION)
        self.wallet = b.wallet
        self.note = b.note
        if b.fee_value:
            self.fees = b.fee_value.quantize(config.PRECISION)
        else:
            self.fees = Decimal(0)
