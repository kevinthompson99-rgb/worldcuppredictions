"""Per-gameweek financial tracking: opt-in stakes, pot sizes, and settlement.

Players opt in to a gameweek (see GameweekEntry) to enter that week's GBP pot. Once every
fixture in the gameweek is finished and scored, the highest-scoring opted-in player(s)
split the pot and everyone else opted-in loses their stake (see `gameweek_financial_summary`
for the exact arithmetic). Nothing here is persisted - it's all derived on the fly from
GameweekEntry + Prediction.points, so a later score correction can't leave stale figures
behind. No real money moves; this is for reference, settled externally between players.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.extensions import db
from app.leaderboards import gameweek_leaderboard, season_standings
from app.models import DEFAULT_STAKE_AMOUNT, GAMEWEEK_STATUS_DRAFT, Gameweek, GameweekEntry


def opted_in_user_ids(gameweek):
    """Set of user ids who are currently opted in to `gameweek`'s pot."""
    return {
        entry.user_id
        for entry in GameweekEntry.query.filter_by(gameweek_id=gameweek.id, opted_in=True)
    }


def is_opted_in(user, gameweek):
    entry = GameweekEntry.query.filter_by(user_id=user.id, gameweek_id=gameweek.id).first()
    return bool(entry and entry.opted_in)


def gameweek_pot(entrant_count, stake=DEFAULT_STAKE_AMOUNT):
    return stake * entrant_count


def set_gameweek_stake(gameweek, amount):
    """Parse/validate and set a gameweek's stake. Raises ValueError on an invalid amount."""
    try:
        parsed = Decimal(amount)
    except (InvalidOperation, TypeError):
        raise ValueError("Enter a valid amount.")
    if parsed <= 0:
        raise ValueError("Stake must be greater than zero.")
    gameweek.stake_amount = parsed.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    db.session.commit()


def _split_pot(pot, num_winners):
    return (pot / num_winners).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def gameweek_financial_summary(gameweek):
    """Pot/settlement details for a gameweek.

    Returns {
        "pot": Decimal, "stake": Decimal, "entrant_count": int, "settled": bool,
        "rows": [{"user", "gameweek_points", "financial_result", "is_winner"}, ...],
    }

    `rows` covers opted-in users only, ranked by gameweek points (highest first).
    `financial_result` is `None` until the gameweek is settled (every fixture finished
    and scored) - the winner can't be determined, and therefore no one has won or
    lost anything yet, before that. Once settled: the winner(s) net `pot/n - stake`
    (their winnings minus the stake they put in) and everyone else nets `-stake` -
    these always sum to zero across the entrant pool.
    """
    stake = gameweek.stake_amount
    entrant_ids = opted_in_user_ids(gameweek)
    pot = gameweek_pot(len(entrant_ids), stake)
    settled = gameweek.all_fixtures_settled

    rows = []
    if entrant_ids:
        ranked = [(user, points) for user, points, _ in gameweek_leaderboard(gameweek) if user.id in entrant_ids]

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
                financial_result = share - stake
            else:
                financial_result = -stake

            rows.append({
                "user": user,
                "gameweek_points": points,
                "financial_result": financial_result,
                "is_winner": is_winner,
            })

    return {
        "pot": pot,
        "stake": stake,
        "entrant_count": len(entrant_ids),
        "settled": settled,
        "rows": rows,
    }


def season_financial_table():
    """Cumulative points + balance for every user who has opted in to at least one gameweek.

    Ordered by cumulative season points (highest first). Balances only include settled
    gameweeks - a gameweek still in progress doesn't move anyone's total yet.
    """
    participated = set()
    balances = {}

    for gameweek in Gameweek.query.order_by(Gameweek.matchday.asc()).all():
        summary = gameweek_financial_summary(gameweek)
        for row in summary["rows"]:
            user_id = row["user"].id
            participated.add(user_id)
            if row["financial_result"] is not None:
                balances[user_id] = balances.get(user_id, Decimal("0")) + row["financial_result"]

    return [
        (user, points, balances.get(user.id, Decimal("0")))
        for user, points in season_standings()
        if user.id in participated
    ]


def all_gameweeks_financial_summary():
    """Per-gameweek financial summaries for every published gameweek, newest first - the
    admin's view. DRAFT gameweeks are excluded - they've never been open for opt-in, so
    there's nothing to settle, and with all 38 auto-created upfront, most of the season
    sits in DRAFT at any given time - showing them here would bury the gameweeks that
    actually have activity under dozens of untouched future ones.
    """
    return [
        (gameweek, gameweek_financial_summary(gameweek))
        for gameweek in Gameweek.query.filter(Gameweek.status != GAMEWEEK_STATUS_DRAFT)
        .order_by(Gameweek.matchday.desc()).all()
    ]
