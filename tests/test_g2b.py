# pylint: disable=missing-docstring
# pylint: disable=attribute-defined-outside-init
# pylint: disable=protected-access
import datetime
import re
from pathlib import Path, PosixPath
from unittest import mock

import pytest
import yaml
from beancount.core import data, amount
from beancount.core.number import D
from beancount.parser import printer
from click.testing import CliRunner

from g2b.g2b import main, GnuCash2Beancount, G2BException


class TestCLI:

    def setup_method(self):
        self.cli_runner = CliRunner()

    @mock.patch("g2b.g2b.GnuCash2Beancount.write_beancount_file")
    def test_cli_calls_write_beancount_file(self, mock_write_beancount_file, tmp_path):
        gnucash_path = tmp_path / "book.gnucash"
        gnucash_path.touch()
        config_path = tmp_path / "config.yaml"
        test_config = {"converter": {"loglevel": "INFO"}}
        config_path.write_text(yaml.dump(test_config))
        command = f"-i {gnucash_path} -o book.beancount -c {config_path}"
        result = self.cli_runner.invoke(main, command.split())
        assert result.exit_code == 0, f"{result.exc_info}"
        mock_write_beancount_file.assert_called()

    def test_cli_raises_on_non_existing_input_file(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        test_config = {"converter": {"loglevel": "INFO"}}
        config_path.write_text(yaml.dump(test_config))
        command = f"-i test.gnucash -o book.beancount -c {config_path}"
        result = self.cli_runner.invoke(main, command.split())
        assert result.exit_code == 2, f"{result.exc_info}"
        assert re.match(r".*Path 'test.gnucash' does not exist.*", result.output, flags=re.DOTALL)

    def test_cli_raises_on_non_existing_config_file(self, tmp_path):
        gnucash_path = tmp_path / "book.gnucash"
        gnucash_path.touch()
        command = f"-i {gnucash_path} -o book.beancount -c test_config.yml"
        result = self.cli_runner.invoke(main, command.split())
        assert result.exit_code == 2, f"{result.exc_info}"
        assert re.match(
            r".*Path 'test_config.yml' does not exist.*", result.output, flags=re.DOTALL
        )


class TestGnuCash2Beancount:

    def setup_method(self):
        self.test_config = {
            "converter": {"loglevel": "INFO"},
            "gnucash": {
                "default_currency": "EUR",
                "thousands_symbol": ".",
                "decimal_symbol": ",",
                "reconciled_symbol": "b",
                "not_reconciled_symbol": "n",
                "account_rename_patterns": [
                    ["Assets:Bank:Some Bank \\(test\\)", "Assets:Bank:Some Test Bank"],
                    ["Assets:Bank:Some USD Bank ", "Assets:Bank:Some Bank (USD)"],
                    ["Expenses:Groceries", "Expenses:MyGroceries"],
                ],
                "non_default_account_currencies": {"Assets:Current-Assets:Wallet-Nzd": "NZD"},
            },
            "beancount": {
                "options": [["operating_currency", "EUR"], ["title", "Exported GnuCash Book"]],
                "plugins": ["beancount.plugins.auto"],
            },
        }
        self.gnucash_path = Path("tests/test_book.gnucash")

    def test_configs_returns_valid_yaml(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(Path(), Path(), config_path)
        assert isinstance(g2b._configs, dict)

    def test_configs_raises_on_invalid_yaml(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("some : invalid : yaml")
        with pytest.raises(G2BException, match="Error while parsing config file"):
            _ = GnuCash2Beancount(Path(), Path(), config_path)

    def test_bean_config_adds_switched_to_beancount_event_if_no_events_are_configured(
        self, tmp_path
    ):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(Path(), Path(), config_path)
        assert g2b._bean_config.get("events") == {
            datetime.date.today(): "misc Changed from GnuCash to Beancount"
        }

    def test_bean_config_adds_switched_to_beancount_event_if_events_are_configured(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        self.test_config["beancount"]["events"] = {datetime.date.today(): "test Test Event"}
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(Path(), Path(), config_path)
        assert g2b._bean_config.get("events") == {
            datetime.date.today(): "test Test Event",
            datetime.date.today(): "misc Changed from GnuCash to Beancount",
        }

    def test_converter_config_returns_only_converter_configurations(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(Path(), Path(), config_path)
        assert isinstance(g2b._converter_config, dict)
        assert g2b._converter_config == self.test_config.get("converter")

    def test_account_rename_patterns_enriches_config_with_default_patterns(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(Path(), Path(), config_path)
        assert (r"\s", "-") in g2b._account_rename_patterns

    def test_write_beancount_file_writes_a_valid_beancount_file(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        output_path = tmp_path / "bean.beancount"
        g2b = GnuCash2Beancount(self.gnucash_path, output_path, config_path)
        g2b.write_beancount_file()
        with open(output_path, "r", encoding="utf8") as beanfile:
            content = beanfile.read()
        example_transaction = """2024-05-06 ! "Transfer"
  ! Assets:Current-Assets:Checkingaccount-Foo-Bank   1000.0 EUR
  ! Assets:Current-Assets:Checking-Account          -1000.0 EUR
"""
        assert example_transaction in content

    def test_get_open_account_directives_creates_beancount_open_objects(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        g2b._read_gnucash_book()
        open_directives = g2b._get_open_account_directives(g2b._get_transactions())
        expected_account_openings = [
            data.Open(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 1).date(),
                account="Assets:Current-Assets:Checking-Account",
                currencies=["EUR"],
                booking=None,
            ),
            data.Open(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 1).date(),
                account="Equity:Opening-Balances",
                currencies=["EUR"],
                booking=None,
            ),
            data.Open(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 3).date(),
                account="Expenses:Mygroceries",
                currencies=["EUR"],
                booking=None,
            ),
            data.Open(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 6).date(),
                account="Assets:Current-Assets:Checkingaccount-Foo-Bank",
                currencies=["EUR"],
                booking=None,
            ),
            data.Open(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 9).date(),
                account="Assets:Current-Assets:Wallet-Nzd",
                currencies=["NZD"],
                booking=None,
            ),
        ]
        assert open_directives == expected_account_openings

    def test_get_transaction_directives_creates_beancount_transaction_objects(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        g2b._read_gnucash_book()
        transactions = g2b._get_transactions()
        expected_transactions = [
            data.Transaction(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 1).date(),
                flag="!",
                payee="",
                narration="Opening",
                tags=frozenset(),
                links=set(),
                postings=[
                    data.Posting(
                        account="Assets:Current-Assets:Checking-Account",
                        units=amount.Amount(D("10000.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag="!",
                        meta=None,
                    ),
                    data.Posting(
                        account="Equity:Opening-Balances",
                        units=amount.Amount(D("-10000.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag="!",
                        meta=None,
                    ),
                ],
            ),
            data.Transaction(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 3).date(),
                flag="!",
                payee="",
                narration="Groceries",
                tags=frozenset(),
                links=set(),
                postings=[
                    data.Posting(
                        account="Expenses:Mygroceries",
                        units=amount.Amount(D("120.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag="!",
                        meta=None,
                    ),
                    data.Posting(
                        account="Assets:Current-Assets:Checking-Account",
                        units=amount.Amount(D("-120.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag="!",
                        meta=None,
                    ),
                ],
            ),
            data.Transaction(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 6).date(),
                flag="!",
                payee="",
                narration="Transfer",
                tags=frozenset(),
                links=set(),
                postings=[
                    data.Posting(
                        account="Assets:Current-Assets:Checkingaccount-Foo-Bank",
                        units=amount.Amount(D("1000.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag="!",
                        meta=None,
                    ),
                    data.Posting(
                        account="Assets:Current-Assets:Checking-Account",
                        units=amount.Amount(D("-1000.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag="!",
                        meta=None,
                    ),
                ],
            ),
            data.Transaction(
                meta={"filename": PosixPath(self.gnucash_path), "lineno": -1},
                date=datetime.datetime(2024, 5, 9).date(),
                flag="!",
                payee="",
                narration="MoneyTransfer",
                tags=frozenset(),
                links=set(),
                postings=[
                    data.Posting(
                        account="Assets:Current-Assets:Wallet-Nzd",
                        units=amount.Amount(D("50.0"), currency="NZD"),
                        cost=None,
                        price=None,
                        flag="!",
                        meta=None,
                    ),
                    data.Posting(
                        account="Assets:Current-Assets:Checking-Account",
                        units=amount.Amount(
                            D("-27.950"),
                            currency="EUR",
                        ),
                        cost=None,
                        price=amount.Amount(
                            D("1.788908765652951699463327370"),
                            currency="NZD",
                        ),
                        flag="!",
                        meta=None,
                    ),
                ],
            ),
        ]
        assert transactions == expected_transactions

    def test_get_header_str_contains_options_and_plugins(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        header = g2b._get_header_str()
        expected_header = """plugin "beancount.plugins.auto"

option "operating_currency" "EUR"
option "title" "Exported GnuCash Book"

"""
        assert header == expected_header

    def test_get_commodities_contains_default_and_non_default_currencies(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        g2b._read_gnucash_book()
        g2b._get_transactions()
        commodities = g2b._get_commodities()
        commodities_str = "\n".join([printer.format_entry(com) for com in commodities])
        expected_commodities = "2024-05-01 commodity EUR\n\n2024-05-09 commodity NZD\n"
        assert commodities_str == expected_commodities

    @mock.patch("g2b.g2b.parse_file")
    def test_verify_output_calls_beancount_parse_file(self, mock_parse, tmp_path):
        mock_parse.return_value = [[], [], {}]
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        g2b._verify_output()
        mock_parse.assert_called()

    @mock.patch("g2b.g2b.parse_file", mock.MagicMock(return_value=[[], [], {}]))
    @mock.patch("g2b.g2b.validate")
    def test_verify_output_calls_beancount_validate(self, mock_validate, tmp_path):
        mock_validate.return_value = {}
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(self.gnucash_path, Path(), config_path)
        g2b._verify_output()
        mock_validate.assert_called()

    def test_events_created_beancount_event_objects(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCash2Beancount(Path(), Path(), config_path)
        events = g2b._get_event_directives()
        assert events == [
            data.Event(
                date=datetime.date.today(),
                type="misc",
                description="Changed from GnuCash to Beancount",
                meta={},
            )
        ]

    def test_read_book_raises_on_wrong_file_format(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        gnucash_file = tmp_path / "book.gnucash"
        gnucash_file.write_text("wrong format")
        g2b = GnuCash2Beancount(gnucash_file, Path(), config_path)
        with pytest.raises(G2BException, match="File does not exist or wrong format exception.*"):
            g2b._read_gnucash_book()
