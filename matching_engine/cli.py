"""Text command adapter for interactive and batch matching-engine use."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import shlex
import sys
from typing import TextIO

from .engine import MatchingEngine
from .exceptions import MatchingEngineError, ValidationError
from .models import BookEntry, BookSnapshot, OperationResult, OrderType, Trade


HELP_LINES = (
    "Commands:",
    "  limit <buy|sell> <price> <qty>",
    "  market <buy|sell> <qty>",
    "  peg <bid|offer> <buy|sell> <qty>",
    "  cancel order <order_id>",
    "  amend order <order_id> [price <price>] [qty <qty>]",
    "  print book",
    "  help",
    "  quit",
)


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """Output produced by one parsed command."""

    lines: tuple[str, ...] = field(default_factory=tuple)
    should_exit: bool = False


class CommandProcessor:
    """Parse and dispatch one command at a time against a shared engine."""

    def __init__(self, engine: MatchingEngine | None = None) -> None:
        self.engine = engine if engine is not None else MatchingEngine()

    def execute(self, line: str) -> CommandOutcome:
        """Execute one command and return printable lines.

        Expected domain and syntax errors are raised as ``MatchingEngineError``
        subclasses. The stream runner decides how to present those errors.
        """

        try:
            tokens = shlex.split(line)
        except ValueError as error:
            raise ValidationError(f"invalid command syntax: {error}") from None
        if not tokens:
            return CommandOutcome()

        command = tokens[0].lower()
        if command == "limit":
            return self._limit(tokens)
        if command == "market":
            return self._market(tokens)
        if command == "peg":
            return self._peg(tokens)
        if command == "cancel":
            return self._cancel(tokens)
        if command == "amend":
            return self._amend(tokens)
        if command == "print":
            return self._print(tokens)
        if command == "help":
            self._require_exact(tokens, 1, "help")
            return CommandOutcome(HELP_LINES)
        if command in {"quit", "exit"}:
            self._require_exact(tokens, 1, "quit")
            return CommandOutcome(should_exit=True)
        raise ValidationError(f"unknown command: {tokens[0]}")

    def _limit(self, tokens: list[str]) -> CommandOutcome:
        self._require_exact(tokens, 4, "limit <buy|sell> <price> <qty>")
        side = tokens[1].lower()
        price = tokens[2]
        quantity = self._parse_quantity(tokens[3])
        result = self.engine.place_limit(side, price, quantity)
        created = (
            f"Order created: {side} {quantity} @ "
            f"{format_price(Decimal(price))} {result.order_id}"
        )
        return CommandOutcome((created, *format_trades(result)))

    def _market(self, tokens: list[str]) -> CommandOutcome:
        self._require_exact(tokens, 3, "market <buy|sell> <qty>")
        side = tokens[1].lower()
        quantity = self._parse_quantity(tokens[2])
        result = self.engine.place_market(side, quantity)
        return CommandOutcome(format_trades(result))

    def _peg(self, tokens: list[str]) -> CommandOutcome:
        self._require_exact(tokens, 4, "peg <bid|offer> <buy|sell> <qty>")
        reference = tokens[1].lower()
        side = tokens[2].lower()
        quantity = self._parse_quantity(tokens[3])
        result = self.engine.place_pegged(side, reference, quantity)
        entry = self._entry_for(result.order_id)
        created = (
            f"Order created: peg {reference} {side} {quantity} @ "
            f"{format_price(entry.price)} {result.order_id}"
        )
        return CommandOutcome((created,))

    def _cancel(self, tokens: list[str]) -> CommandOutcome:
        self._require_exact(tokens, 3, "cancel order <order_id>")
        if tokens[1].lower() != "order":
            raise ValidationError("usage: cancel order <order_id>")
        self.engine.cancel(tokens[2])
        return CommandOutcome(("Order cancelled",))

    def _amend(self, tokens: list[str]) -> CommandOutcome:
        if len(tokens) < 5 or len(tokens[3:]) % 2:
            raise ValidationError(
                "usage: amend order <order_id> [price <price>] [qty <qty>]"
            )
        if tokens[1].lower() != "order":
            raise ValidationError(
                "usage: amend order <order_id> [price <price>] [qty <qty>]"
            )

        fields: dict[str, Decimal | str | int] = {}
        for index in range(3, len(tokens), 2):
            name = tokens[index].lower()
            if name not in {"price", "qty"}:
                raise ValidationError(f"unknown amendment field: {tokens[index]}")
            if name in fields:
                raise ValidationError(f"duplicate amendment field: {name}")
            value = tokens[index + 1]
            fields[name] = self._parse_quantity(value) if name == "qty" else value

        result = self.engine.amend(
            tokens[2],
            price=fields.get("price"),
            quantity=fields.get("qty"),
        )
        return CommandOutcome(("Order amended", *format_trades(result)))

    def _print(self, tokens: list[str]) -> CommandOutcome:
        self._require_exact(tokens, 2, "print book")
        if tokens[1].lower() != "book":
            raise ValidationError("usage: print book")
        return CommandOutcome(format_book(self.engine.snapshot()))

    def _entry_for(self, order_id: str | None) -> BookEntry:
        snapshot = self.engine.snapshot()
        for entry in (*snapshot.bids, *snapshot.offers):
            if entry.order_id == order_id:
                return entry
        raise RuntimeError("newly created pegged order is missing from the book")

    @staticmethod
    def _parse_quantity(value: str) -> int:
        try:
            return int(value)
        except ValueError:
            raise ValidationError("quantity must be an integer") from None

    @staticmethod
    def _require_exact(tokens: list[str], count: int, usage: str) -> None:
        if len(tokens) != count:
            raise ValidationError(f"usage: {usage}")


def format_price(price: Decimal) -> str:
    """Render a Decimal without scientific notation or redundant zeroes."""

    rendered = format(price, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def format_trades(result: OperationResult) -> tuple[str, ...]:
    """Render the exercise's required line for every reported trade."""

    return tuple(
        f"Trade, price: {format_price(trade.price)}, qty: {trade.qty}"
        for trade in result.trades
    )


def format_book(snapshot: BookSnapshot) -> tuple[str, ...]:
    """Render individual orders in a two-sided, price-time ordered table."""

    bids = [_format_entry(entry) for entry in snapshot.bids] or ["(empty)"]
    offers = [_format_entry(entry) for entry in snapshot.offers] or ["(empty)"]
    left_width = max(len("Buy orders"), *(len(entry) for entry in bids))
    right_width = max(len("Sell orders"), *(len(entry) for entry in offers))
    lines = [
        f"{'Buy orders':<{left_width}} | Sell orders",
        f"{'-' * left_width}-+-{'-' * right_width}",
    ]
    for index in range(max(len(bids), len(offers))):
        bid = bids[index] if index < len(bids) else ""
        offer = offers[index] if index < len(offers) else ""
        lines.append(f"{bid:<{left_width}} | {offer}".rstrip())
    return tuple(lines)


def _format_entry(entry: BookEntry) -> str:
    annotation = ""
    if entry.order_type is OrderType.PEGGED:
        annotation = f"; peg {entry.peg_reference.value}"
    return (
        f"{entry.quantity} @ {format_price(entry.price)} "
        f"[{entry.order_id}{annotation}]"
    )


def run(stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    """Run until EOF or a quit command, using prompts only for a TTY."""

    input_stream = sys.stdin if stdin is None else stdin
    output_stream = sys.stdout if stdout is None else stdout
    interactive = input_stream.isatty()
    processor = CommandProcessor()

    while True:
        if interactive:
            output_stream.write(">>> ")
            output_stream.flush()
        try:
            line = input_stream.readline()
        except KeyboardInterrupt:
            if interactive:
                output_stream.write("\n")
            return 130
        if line == "":
            if interactive:
                output_stream.write("\n")
            break

        try:
            outcome = processor.execute(line)
        except MatchingEngineError as error:
            output_stream.write(f"Error: {error}\n")
            continue

        for output_line in outcome.lines:
            output_stream.write(f"{output_line}\n")
        if outcome.should_exit:
            break

    return 0


def main() -> int:
    """Console entry point."""

    return run()


__all__ = [
    "CommandOutcome",
    "CommandProcessor",
    "format_book",
    "format_price",
    "format_trades",
    "main",
    "run",
]
