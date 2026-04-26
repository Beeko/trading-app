"""
Options strategy selector — maps trade signals to appropriate
low-risk options strategies based on direction, risk profile, and Greeks.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from src.data.market_data import OptionContract
from src.strategy.signals import TradeSignal
from config import settings
from config.risk_profiles import get_profile
from src.utils.logger import get_logger

log = get_logger("options")


@dataclass
class OptionsPlay:
    """A concrete options trade recommendation."""
    strategy_name: str        # e.g. "bull_call_spread"
    legs: list[dict]          # each leg: {contract, action, quantity}
    max_profit: float
    max_loss: float           # this is always defined (no unlimited risk)
    breakeven: float
    risk_reward_ratio: float
    capital_required: float
    reasoning: str


class OptionsStrategySelector:
    """Selects safe options strategies based on signals and risk profile."""

    def __init__(self):
        self._profile = get_profile(settings.RISK_PROFILE)
        log.info(f"OptionsStrategySelector initialized "
                 f"(profile={self._profile.name})")

    def select_strategy(
        self,
        signal: TradeSignal,
        contracts: list[OptionContract],
        stock_price: float,
        account_balance: float,
    ) -> Optional[OptionsPlay]:
        """
        Given a trade signal and available options contracts,
        select the best low-risk options strategy.
        """
        if not contracts:
            log.debug(f"No contracts available for {signal.ticker}")
            return None

        # Filter contracts by minimum DTE and delta
        valid = self._filter_contracts(contracts)
        if not valid:
            log.debug(f"No contracts passed filters for {signal.ticker}")
            return None

        # Separate calls and puts
        calls = [c for c in valid if c.option_type == "call"]
        puts = [c for c in valid if c.option_type == "put"]

        if signal.direction == "buy":
            # Bullish strategies
            play = (
                self._bull_call_spread(calls, stock_price, account_balance)
                or self._cash_secured_put(puts, stock_price, account_balance)
            )
        else:
            # Bearish strategies
            play = (
                self._bear_put_spread(puts, stock_price, account_balance)
                or self._protective_put(puts, stock_price, account_balance)
            )

        if play and play.strategy_name not in settings.ALLOWED_OPTIONS_STRATEGIES:
            log.warning(
                f"Strategy {play.strategy_name} not in whitelist, skipping"
            )
            return None

        return play

    def _filter_contracts(
        self, contracts: list[OptionContract]
    ) -> list[OptionContract]:
        """Apply risk profile filters to contracts."""
        return [
            c for c in contracts
            if abs(c.delta) <= self._profile.max_options_delta
            and self._dte(c) >= self._profile.min_days_to_expiry
            and c.open_interest >= 10  # minimum liquidity
            and c.bid > 0  # must have a market
        ]

    def _bull_call_spread(
        self,
        calls: list[OptionContract],
        stock_price: float,
        account_balance: float,
    ) -> Optional[OptionsPlay]:
        """
        Buy a call, sell a higher-strike call.
        Max loss = net debit. Max profit = strike difference - debit.
        """
        # Find ATM call to buy
        buy_call = min(
            calls,
            key=lambda c: abs(c.strike - stock_price),
            default=None,
        )
        if not buy_call:
            return None

        # Find OTM call to sell (one strike higher)
        sell_candidates = [
            c for c in calls
            if c.strike > buy_call.strike
            and c.expiration == buy_call.expiration
        ]
        if not sell_candidates:
            return None

        sell_call = min(sell_candidates, key=lambda c: c.strike)

        net_debit = buy_call.ask - sell_call.bid
        if net_debit <= 0:
            return None

        max_loss = net_debit * 100  # per contract
        max_profit = (sell_call.strike - buy_call.strike - net_debit) * 100
        if max_profit <= 0:
            return None

        # Check if capital required is within limits
        allocation = account_balance * self._profile.max_single_trade_risk_pct / 100
        if max_loss > allocation:
            return None

        return OptionsPlay(
            strategy_name="bull_call_spread",
            legs=[
                {"contract": buy_call.contract_symbol,
                 "action": "buy", "quantity": 1},
                {"contract": sell_call.contract_symbol,
                 "action": "sell", "quantity": 1},
            ],
            max_profit=round(max_profit, 2),
            max_loss=round(max_loss, 2),
            breakeven=round(buy_call.strike + net_debit, 2),
            risk_reward_ratio=round(max_profit / max_loss, 2) if max_loss > 0 else 0,
            capital_required=round(max_loss, 2),
            reasoning=(
                f"Bull call spread: buy ${buy_call.strike} call, "
                f"sell ${sell_call.strike} call. "
                f"Risk/reward {max_profit/max_loss:.1f}:1"
            ),
        )

    def _cash_secured_put(
        self,
        puts: list[OptionContract],
        stock_price: float,
        account_balance: float,
    ) -> Optional[OptionsPlay]:
        """
        Sell an OTM put, secured by cash to buy shares if assigned.
        Income strategy — get paid to wait for a lower price.
        """
        # Find OTM put (5-10% below current price)
        target_strike = stock_price * 0.93
        candidates = [
            p for p in puts
            if p.strike <= stock_price * 0.97
            and p.strike >= stock_price * 0.85
        ]
        if not candidates:
            return None

        put = min(candidates, key=lambda p: abs(p.strike - target_strike))

        premium = put.bid * 100
        if premium < 10:
            return None

        capital_required = put.strike * 100
        allocation = account_balance * self._profile.max_position_pct / 100
        if capital_required > allocation:
            return None

        return OptionsPlay(
            strategy_name="cash_secured_put",
            legs=[
                {"contract": put.contract_symbol,
                 "action": "sell", "quantity": 1},
            ],
            max_profit=round(premium, 2),
            max_loss=round(capital_required - premium, 2),
            breakeven=round(put.strike - put.bid, 2),
            risk_reward_ratio=round(premium / (capital_required - premium), 2),
            capital_required=round(capital_required, 2),
            reasoning=(
                f"Cash-secured put: sell ${put.strike} put for "
                f"${premium:.0f} premium. Willing to buy at "
                f"${put.strike} if assigned."
            ),
        )

    def _bear_put_spread(
        self,
        puts: list[OptionContract],
        stock_price: float,
        account_balance: float,
    ) -> Optional[OptionsPlay]:
        """
        Buy a put, sell a lower-strike put.
        Bearish defined-risk play.
        """
        buy_put = min(
            puts,
            key=lambda p: abs(p.strike - stock_price),
            default=None,
        )
        if not buy_put:
            return None

        sell_candidates = [
            p for p in puts
            if p.strike < buy_put.strike
            and p.expiration == buy_put.expiration
        ]
        if not sell_candidates:
            return None

        sell_put = max(sell_candidates, key=lambda p: p.strike)

        net_debit = buy_put.ask - sell_put.bid
        if net_debit <= 0:
            return None

        max_loss = net_debit * 100
        max_profit = (buy_put.strike - sell_put.strike - net_debit) * 100
        if max_profit <= 0:
            return None

        allocation = account_balance * self._profile.max_single_trade_risk_pct / 100
        if max_loss > allocation:
            return None

        return OptionsPlay(
            strategy_name="bear_put_spread",
            legs=[
                {"contract": buy_put.contract_symbol,
                 "action": "buy", "quantity": 1},
                {"contract": sell_put.contract_symbol,
                 "action": "sell", "quantity": 1},
            ],
            max_profit=round(max_profit, 2),
            max_loss=round(max_loss, 2),
            breakeven=round(buy_put.strike - net_debit, 2),
            risk_reward_ratio=round(max_profit / max_loss, 2) if max_loss > 0 else 0,
            capital_required=round(max_loss, 2),
            reasoning=(
                f"Bear put spread: buy ${buy_put.strike} put, "
                f"sell ${sell_put.strike} put. "
                f"Risk/reward {max_profit/max_loss:.1f}:1"
            ),
        )

    def _protective_put(
        self,
        puts: list[OptionContract],
        stock_price: float,
        account_balance: float,
    ) -> Optional[OptionsPlay]:
        """Buy a put as insurance on existing long position."""
        target_strike = stock_price * 0.95
        candidates = [
            p for p in puts
            if p.strike <= stock_price
            and p.strike >= stock_price * 0.90
        ]
        if not candidates:
            return None

        put = min(candidates, key=lambda p: abs(p.strike - target_strike))
        cost = put.ask * 100

        return OptionsPlay(
            strategy_name="protective_put",
            legs=[
                {"contract": put.contract_symbol,
                 "action": "buy", "quantity": 1},
            ],
            max_profit=0,  # unlimited upside on underlying
            max_loss=round(cost + (stock_price - put.strike) * 100, 2),
            breakeven=round(stock_price + put.ask, 2),
            risk_reward_ratio=0,
            capital_required=round(cost, 2),
            reasoning=(
                f"Protective put: buy ${put.strike} put for "
                f"${cost:.0f}. Limits downside below ${put.strike}."
            ),
        )

    @staticmethod
    def _dte(contract: OptionContract) -> int:
        """Days to expiration."""
        try:
            exp = datetime.strptime(contract.expiration, "%Y-%m-%d")
            return max((exp - datetime.now(timezone.utc).replace(tzinfo=None)).days, 0)
        except (ValueError, TypeError):
            return 0
