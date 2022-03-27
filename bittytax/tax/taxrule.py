class TaxRule(object):
    def __init__(self, name, matcher):
        if not callable(matcher):
            raise ValueError("Matcher must be a function")

        self.name = name
        self.matcher = matcher

    def match(self, sell, buy):
        return self.matcher(sell, buy)
