import io
from pathlib import Path
import subprocess
import sys
import unittest

from matching_engine import MatchingEngine, ValidationError
from matching_engine.cli import CommandProcessor, format_book, format_price, run


class TTYInput(io.StringIO):
    def isatty(self):
        return True


class CommandProcessorTests(unittest.TestCase):
    def setUp(self):
        self.processor = CommandProcessor(MatchingEngine())

    def test_limit_market_and_trade_output(self):
        created = self.processor.execute("limit sell 20.00 100")
        traded = self.processor.execute("market BUY 40")

        self.assertEqual(
            created.lines,
            ("Order created: sell 100 @ 20 order_000001",),
        )
        self.assertEqual(traded.lines, ("Trade, price: 20, qty: 40",))

    def test_crossing_limit_reports_creation_and_trades(self):
        self.processor.execute("limit sell 10.5 10")

        outcome = self.processor.execute("limit buy 11 15")

        self.assertEqual(
            outcome.lines,
            (
                "Order created: buy 15 @ 11 order_000002",
                "Trade, price: 10.5, qty: 10",
            ),
        )

    def test_peg_cancel_and_amend_commands(self):
        self.processor.execute("limit buy 10 100")
        peg = self.processor.execute("peg BID BUY 25")

        self.assertEqual(
            peg.lines,
            ("Order created: peg bid buy 25 @ 10 order_000002",),
        )
        self.assertEqual(
            self.processor.execute("amend order order_000002 qty 20").lines,
            ("Order amended",),
        )
        self.assertEqual(
            self.processor.execute("cancel order order_000002").lines,
            ("Order cancelled",),
        )

    def test_amend_fields_work_in_either_order_and_can_trade(self):
        self.processor.execute("limit sell 10 10")
        order = self.processor.execute("limit buy 9 15")
        order_id = order.lines[0].split()[-1]

        outcome = self.processor.execute(
            f"amend order {order_id} qty 12 price 10"
        )

        self.assertEqual(
            outcome.lines,
            ("Order amended", "Trade, price: 10, qty: 10"),
        )

    def test_print_book_includes_ids_and_peg_annotations(self):
        self.processor.execute("limit buy 10 100")
        self.processor.execute("peg bid buy 25")
        self.processor.execute("limit sell 10.5 50")

        lines = self.processor.execute("print book").lines

        self.assertIn("Buy orders", lines[0])
        self.assertIn("Sell orders", lines[0])
        self.assertIn("100 @ 10 [order_000001]", lines[2])
        self.assertIn("50 @ 10.5 [order_000003]", lines[2])
        self.assertIn("25 @ 10 [order_000002; peg bid]", lines[3])

    def test_help_blank_and_quit(self):
        self.assertEqual(self.processor.execute("   ").lines, ())
        self.assertIn("Commands:", self.processor.execute("help").lines)
        self.assertTrue(self.processor.execute("exit").should_exit)

    def test_invalid_syntax_raises_domain_errors(self):
        invalid_commands = (
            "unknown",
            "limit buy 10",
            "market buy ten",
            "peg offer buy 5",
            "cancel order",
            "cancel id order_000001",
            "amend order order_000001",
            "amend order order_000001 size 2",
            "amend order order_000001 qty 2 qty 3",
            "print orders",
            'limit buy "10 5',
        )
        for command in invalid_commands:
            with self.subTest(command=command):
                with self.assertRaises(ValidationError):
                    self.processor.execute(command)


class FormattingTests(unittest.TestCase):
    def test_price_format_avoids_scientific_notation_and_redundant_zeroes(self):
        from decimal import Decimal

        self.assertEqual(format_price(Decimal("10.1000")), "10.1")
        self.assertEqual(format_price(Decimal("1E+3")), "1000")
        self.assertEqual(format_price(Decimal("0.0100")), "0.01")

    def test_empty_book_is_explicit(self):
        lines = format_book(MatchingEngine().snapshot())
        self.assertIn("(empty)", lines[2])


class StreamRunnerTests(unittest.TestCase):
    def test_batch_mode_has_no_prompt_and_continues_after_error(self):
        stdin = io.StringIO(
            "limit buy 10 100\n"
            "not-a-command\n"
            "limit sell 10 40\n"
            "print book\n"
        )
        stdout = io.StringIO()

        exit_code = run(stdin, stdout)
        output = stdout.getvalue()

        self.assertEqual(exit_code, 0)
        self.assertNotIn(">>>", output)
        self.assertIn("Order created: buy 100 @ 10 order_000001", output)
        self.assertIn("Error: unknown command: not-a-command", output)
        self.assertIn("Trade, price: 10, qty: 40", output)
        self.assertIn("60 @ 10 [order_000001]", output)

    def test_interactive_mode_prompts_until_quit(self):
        stdin = TTYInput("help\nquit\n")
        stdout = io.StringIO()

        self.assertEqual(run(stdin, stdout), 0)
        self.assertEqual(stdout.getvalue().count(">>> "), 2)
        self.assertIn("Commands:\n", stdout.getvalue())

    def test_interactive_eof_finishes_the_prompt_line(self):
        stdout = io.StringIO()

        self.assertEqual(run(TTYInput(""), stdout), 0)
        self.assertEqual(stdout.getvalue(), ">>> \n")

    def test_module_entry_point_accepts_redirected_input(self):
        repository = Path(__file__).resolve().parents[1]
        completed = subprocess.run(
            [sys.executable, "-m", "matching_engine"],
            cwd=repository,
            input="limit sell 20 100\nmarket buy 25\nquit\n",
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        self.assertNotIn(">>>", completed.stdout)
        self.assertIn("Order created: sell 100 @ 20 order_000001", completed.stdout)
        self.assertIn("Trade, price: 20, qty: 25", completed.stdout)


if __name__ == "__main__":
    unittest.main()
