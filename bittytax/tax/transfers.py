# -*- coding: utf-8 -*-
# (c) Steven Mirabito 2026
"""Pair withdrawals with the deposits they became, so lots can follow them between wallets."""

import re
from datetime import timedelta
from decimal import Decimal

import dateutil.parser

from ..config import config
from ..transactions import Buy, Sell

TRANSFER_TOKEN = re.compile(r'(?:^|\s)transfer:(\S+)')
ACQUIRED_TOKEN = re.compile(r'(?:^|\s)acquired:(\S+)')
GIFT_TOKENS = {name: re.compile(r'(?:^|\s)%s:(\S+)' % name)
               for name in ('donor_basis', 'donor_acquired', 'gift_tax_paid')}


def note_token(pattern, note):
    match = pattern.search(note or '')
    return match.group(1) if match else None


def acquired_date(t):
    """Acquisition date declared in the note of a deposit from outside the records."""
    token = note_token(ACQUIRED_TOKEN, t.note)
    if token is None:
        return None
    return parse_date(token)


def parse_date(token):
    """Date or timestamp from a note token, local time unless a zone is given."""
    parsed = dateutil.parser.parse(token)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=config.TZ_LOCAL)
    return parsed.astimezone(config.TZ_LOCAL)


def tid_key(t):
    return tuple(t.tid)


class TransferLinks(object):
    """Withdrawal <-> deposit pairs, keyed by transaction ID."""

    def __init__(self):
        self.deposit_for = {}
        self.withdrawal_for = {}
        self.errors = []

    def link(self, withdrawal, deposit):
        self.deposit_for[tid_key(withdrawal)] = deposit
        self.withdrawal_for[tid_key(deposit)] = withdrawal

    def deposit(self, withdrawal):
        return self.deposit_for.get(tid_key(withdrawal))

    def is_linked(self, t):
        return tid_key(t) in self.deposit_for or tid_key(t) in self.withdrawal_for


def link_transfers(transactions):
    """Link Withdrawal and Deposit transactions.

    A transfer:<id> token in the note links the two explicitly. Otherwise a withdrawal is
    matched to a deposit of the same asset into a different wallet, which arrives within
    transfer_match_window_hours of it, for the same quantity or slightly less (network fees
    taken from the amount sent, within transfer_match_tolerance).
    """
    links = TransferLinks()
    withdrawals = [t for t in transactions
                   if isinstance(t, Sell) and t.t_type == Sell.TYPE_WITHDRAWAL]
    deposits = [t for t in transactions
                if isinstance(t, Buy) and t.t_type == Buy.TYPE_DEPOSIT]

    _link_explicit(links, withdrawals, deposits)

    window = timedelta(hours=config.transfer_match_window_hours)
    tolerance = Decimal(str(config.transfer_match_tolerance))
    unlinked_deposits = [d for d in deposits if not links.is_linked(d)
                         and note_token(TRANSFER_TOKEN, d.note) is None]

    for w in sorted(withdrawals, key=lambda t: (t.timestamp, t.tid)):
        if links.is_linked(w) or note_token(TRANSFER_TOKEN, w.note) is not None:
            continue

        candidates = [d for d in unlinked_deposits
                      if d.asset == w.asset
                      and d.wallet != w.wallet
                      and w.timestamp <= d.timestamp <= w.timestamp + window
                      and d.quantity <= w.quantity
                      and w.quantity - d.quantity <= w.quantity * tolerance]
        if candidates:
            d = min(candidates, key=lambda d: (d.timestamp - w.timestamp,
                                               w.quantity - d.quantity, d.tid))
            links.link(w, d)
            unlinked_deposits.remove(d)

    return links


def _link_explicit(links, withdrawals, deposits):
    tagged = {}
    for t in withdrawals + deposits:
        token = note_token(TRANSFER_TOKEN, t.note)
        if token is not None:
            tagged.setdefault(token, []).append(t)

    for token, group in sorted(tagged.items()):
        w = [t for t in group if isinstance(t, Sell)]
        d = [t for t in group if isinstance(t, Buy)]
        if len(w) != 1 or len(d) != 1:
            links.errors.append("transfer:%s must tag exactly one Withdrawal and one Deposit "
                                "(found %d and %d)" % (token, len(w), len(d)))
        elif w[0].asset != d[0].asset or d[0].quantity > w[0].quantity:
            links.errors.append("transfer:%s Deposit of %s %s doesn't match Withdrawal of %s %s"
                                % (token, d[0].quantity, d[0].asset,
                                   w[0].quantity, w[0].asset))
        else:
            links.link(w[0], d[0])
