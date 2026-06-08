"""Per-round financial tracking: opt-in stakes, pot sizes, and settlement.

Players opt in to a round (see RoundEntry) to enter that week's GBP 5 pot. Once every
fixture in the round is finished and scored, the highest-scoring opted-in player(s)
split the pot and everyone else opted-in loses their stake (see `round_financial_summary`
for the exact arithmetic). Nothing here is persisted - it's all derived on the fly from
RoundEntry + Prediction.points, so a later score correction can't leave stale figures
behind. No real money moves; this is for reference, settled externally between players.
"""

from decimal import ROUND_HALF_UP, Decimal

from app.leaderboards import round_leaderboard, tournament_standings
from app.models import Round, RoundEntry

STAKE_AMOUNT = Decimal("5.00")


def opted_in_user_ids(round_):
    """Set of user ids who are currently opted in to `round_`'s pot."""
    return {
        entry.user_id
        for entry in RoundEntry.query.filter_by(round_id=round_.id, opted_in=True)
    }


def is_opted_in(user, round_):
    entry = RoundEntry.query.filter_by(user_id=user.id, round_id=round_.id).first()
    return bool(entry and entry.opted_in)


def round_pot(entrant_count):
    return STAKE_AMOUNT * entrant_count


def _split_pot(pot, num_winners):
    return (pot / num_winners).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def round_financial_summary(round_):
    """Pot/settlement details for a round.

    Returns {
        "pot": Decimal, "stake": Decimal, "entrant_count": int, "settled": bool,
        "rows": [{"user", "round_points", "financial_result", "is_winner"}, ...],
    }

    `rows` covers opted-in users only, ranked by round points (highest first).
    `financial_result` is `None` until the round is settled (every fixture finished
    and scored) - the winner can't be determined, and therefore no one has won or
    lost anything yet, before that. Once settled: the winner(s) net `pot/n - stake`
    (their winnings minus the stake they put in) and everyone else nets `-stake` -
    these always sum to zero across the entrant pool.
    """
    entrant_ids = opted_in_user_ids(round_)
    pot = round_pot(len(entrant_ids))
    settled = round_.all_fixtures_settled

    rows = []
    if entrant_ids:
        ranked = [(user, points) for user, points, _ in round_leaderboard(round_) if user.id in entrant_ids]

        winner_ids = set()
        share = None
        if settled and ranked:
            top_score = ranked[0][1]
            winner_ids = {user.id for user, points in ranked if points == top_score}
            share = _split_pot(pot, len(winner_ids))

        for user, points in ranked:
            is_winner = user.id in winner_ids
            if not settled:
                financial_result = None
            elif is_winner:
                financial_result = share - STAKE_AMOUNT
            else:
                financial_result = -STAKE_AMOUNT

            rows.append({
                "user": user,
                "round_points": points,
                "financial_result": financial_result,
                "is_winner": is_winner,
            })

    return {
        "pot": pot,
        "stake": STAKE_AMOUNT,
        "entrant_count": len(entrant_ids),
        "settled": settled,
        "rows": rows,
    }


def season_financial_table():
    """Cumulative points + balance for every user who has opted in to at least one round.

    Ordered by cumulative tournament points (highest first). Balances only include
    settled rounds - a round still in progress doesn't move anyone's total yet.
    """
    participated = set()
    balances = {}

    for round_ in Round.query.order_by(Round.sequence.asc()).all():
        summary = round_financial_summary(round_)
        for row in summary["rows"]:
            user_id = row["user"].id
            participated.add(user_id)
            if row["financial_result"] is not None:
                balances[user_id] = balances.get(user_id, Decimal("0")) + row["financial_result"]

    return [
        (user, points, balances.get(user.id, Decimal("0")))
        for user, points in tournament_standings()
        if user.id in participated
    ]


def all_rounds_financial_summary():
    """Per-round financial summaries for every round, newest first - the admin's view."""
    return [
        (round_, round_financial_summary(round_))
        for round_ in Round.query.order_by(Round.sequence.desc()).all()
    ]
