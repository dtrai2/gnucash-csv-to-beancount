import datetime
import re
from pathlib import Path, PosixPath
from unittest import mock

import pandas.api.types
import pytest
import yaml
from beancount.core import data, amount
from beancount.core.number import D
from beancount.parser.grammar import valid_account_regexp
from click.testing import CliRunner

from g2b.g2b import main, GnuCashCSV2Beancount, G2BException


class TestCLI:

    def setup_method(self):
        self.cli_runner = CliRunner()

    @mock.patch("g2b.g2b.GnuCashCSV2Beancount.write_beancount_file")
    def test_cli_calls_write_beancount_file(self, mock_write_beancount_file, tmp_path):
        gnucash_csv_path = tmp_path / "gnucash.csv"
        gnucash_csv_path.touch()
        config_path = tmp_path / "config.yaml"
        test_config = {"converter": {"loglevel": "INFO"}}
        config_path.write_text(yaml.dump(test_config))
        command = f"-i {gnucash_csv_path} -o book.beancount -c {config_path}"
        result = self.cli_runner.invoke(main, command.split())
        assert result.exit_code == 0, f"{result.exc_info}"
        mock_write_beancount_file.assert_called()

    def test_cli_raises_on_non_existing_input_file(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        test_config = {"converter": {"loglevel": "INFO"}}
        config_path.write_text(yaml.dump(test_config))
        command = f"-i test_gnucash.csv -o book.beancount -c {config_path}"
        result = self.cli_runner.invoke(main, command.split())
        assert result.exit_code == 2, f"{result.exc_info}"
        assert re.match(
            r".*Path 'test_gnucash.csv' does not exist.*", result.output, flags=re.DOTALL
        )

    def test_cli_raises_on_non_existing_config_file(self, tmp_path):
        gnucash_csv_path = tmp_path / "gnucash.csv"
        gnucash_csv_path.touch()
        command = f"-i {gnucash_csv_path} -o book.beancount -c test_config.yml"
        result = self.cli_runner.invoke(main, command.split())
        assert result.exit_code == 2, f"{result.exc_info}"
        assert re.match(
            r".*Path 'test_config.yml' does not exist.*", result.output, flags=re.DOTALL
        )


class TestGnuCashCSV2Beancount:

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
        self.test_gnucash_csv = """
"Datum","BuchungsID","Nummer","Beschreibung","Bemerkungen","Währung/Wertpapier","Stornierungsbegründung","Aktion","Buchungstext","Volle Kontobezeichnung","Kontobezeichnung","Wert mit Symbol","Wert numerisch.","Wert mit Symbol","Wert numerisch.","Abgleichen","Datum des Abgleichs","Kurs/Preis"
"01.05.2024","5402cb3842794f8184295a3b74e229d0","","Opening","","CURRENCY::EUR","","","","Assets:Current Assets:Checking Account","Checking Account","10.000,00 €","10.000,00","10.000,00 €","10.000,00","n","","1,0000"
"01.05.2024","5402cb3842794f8184295a3b74e229d0","","Opening","","CURRENCY::EUR","","","","Equity:Opening Balances","Opening Balances","-10.000,00 €","-10.000,00","-10.000,00 €","-10.000,00","n","","1,0000"
"03.05.2024","f1fc057ef504470f85712d10ce5c34db","","Groceries","","CURRENCY::EUR","","","","Assets:Current Assets:Checking Account","Checking Account","-120,00 €","-120,00","-120,00 €","-120,00","n","","1,0000"
"03.05.2024","f1fc057ef504470f85712d10ce5c34db","","Groceries","","CURRENCY::EUR","","","","Expenses:Groceries","Groceries","120,00 €","120,00","120,00 €","120,00","n","","1,0000"
"09.05.2024","feebbd5bb02a483da3f0b608a0544e89","","MoneyTransfer","","CURRENCY::NZD","","","","Assets:Current Assets:Checking Account","Checking Account","-27,95 €","-27,95","-50,00 NZ$","-50,00","n","","1 + 441/559"
"09.05.2024","feebbd5bb02a483da3f0b608a0544e89","","MoneyTransfer","","CURRENCY::NZD","","","","Assets:Current Assets:Wallet (NZD)","Wallet (NZD)","50,00 NZ$","50,00","50,00 NZ$","50,00","n","","1,0000"
"06.05.2024","6ed68bc4dfbc44d9ab4651b50272251a","","Transfer","","CURRENCY::EUR","","","","Assets:Current Assets:Checking Account","Checking Account","-1.000,00 €","-1.000,00","-1.000,00 €","-1.000,00","n","","1,0000"
"06.05.2024","6ed68bc4dfbc44d9ab4651b50272251a","","Transfer","","CURRENCY::EUR","","","","Assets:Current Assets:CheckingAccount (Foo&Bank)","CheckingAccount (Foo&Bank)","1.000,00 €","1.000,00","1.000,00 €","1.000,00","n","","1,0000"
        """

    def test_configs_returns_valid_yaml(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCashCSV2Beancount(Path(), Path(), config_path)
        assert isinstance(g2b._configs, dict)

    def test_configs_raises_on_invalid_yaml(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text("some : invalid : yaml")
        with pytest.raises(G2BException, match="Error while parsing config file"):
            _ = GnuCashCSV2Beancount(Path(), Path(), config_path)

    def test_converter_config_returns_only_converter_configurations(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCashCSV2Beancount(Path(), Path(), config_path)
        assert isinstance(g2b._converter_config, dict)
        assert g2b._converter_config == self.test_config.get("converter")

    def test_account_rename_patterns_enriches_config_with_default_patterns(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        g2b = GnuCashCSV2Beancount(Path(), Path(), config_path)
        assert (r"\s", "-") in g2b._account_rename_patterns

    def test_prepare_csv_renames_columns(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        gnucash_path = tmp_path / "gnucash.csv"
        gnucash_path.write_text(self.test_gnucash_csv)
        g2b = GnuCashCSV2Beancount(gnucash_path, Path(), config_path)
        g2b._prepare_csv()
        assert "Datum" not in g2b._dataframe.columns
        required_columns = [
            "Date",
            "FullAccountName",
            "Rate",
            "ValueNumerical",
            "BookingID",
            "Description",
            "Reconciliation",
        ]
        for required_col in required_columns:
            assert required_col in g2b._dataframe.columns

    def test_prepare_csv_renames_account_names(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        gnucash_path = tmp_path / "gnucash.csv"
        gnucash_path.write_text(self.test_gnucash_csv)
        g2b = GnuCashCSV2Beancount(gnucash_path, Path(), config_path)
        g2b._prepare_csv()
        account_names = g2b._dataframe["FullAccountName"].unique()
        valid_pattern = valid_account_regexp(
            {
                "name_assets": "Assets",
                "name_liabilities": "Liabilities",
                "name_equity": "Equity",
                "name_income": "Income",
                "name_expenses": "Expenses",
            }
        )
        for account in account_names:
            assert re.match(valid_pattern, account)
        assert "Expenses:Mygroceries" in account_names
        assert "Expenses:Groceries" not in account_names

    @pytest.mark.parametrize(
        "column, assert_dtype",
        [
            ("Date", pandas.api.types.is_datetime64_dtype),
            ("FullAccountName", pandas.api.types.is_string_dtype),
            ("Rate", pandas.api.types.is_numeric_dtype),
            ("ValueNumerical", pandas.api.types.is_numeric_dtype),
            ("BookingID", pandas.api.types.is_string_dtype),
            ("Description", pandas.api.types.is_string_dtype),
            ("Reconciliation", pandas.api.types.is_string_dtype),
        ],
    )
    def test_prepare_csv_ensures_correct_dtypes(self, column, assert_dtype, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        gnucash_path = tmp_path / "gnucash.csv"
        gnucash_path.write_text(self.test_gnucash_csv)
        g2b = GnuCashCSV2Beancount(gnucash_path, Path(), config_path)
        g2b._prepare_csv()
        assert assert_dtype(g2b._dataframe[column]), f"{column} should be {assert_dtype}"

    def test_prepare_csv_sorts_dataframe(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        gnucash_path = tmp_path / "gnucash.csv"
        gnucash_path.write_text(self.test_gnucash_csv)
        g2b = GnuCashCSV2Beancount(gnucash_path, Path(), config_path)
        g2b._prepare_csv()
        assert g2b._dataframe["Date"].is_monotonic_increasing

    def test_write_beancount_file_writes_a_valid_beancount_file(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        gnucash_path = tmp_path / "gnucash.csv"
        gnucash_path.write_text(self.test_gnucash_csv)
        output_path = tmp_path / "bean.beancount"
        g2b = GnuCashCSV2Beancount(gnucash_path, output_path, config_path)
        g2b.write_beancount_file()
        with open(output_path, "r", encoding="utf8") as beanfile:
            content = beanfile.read()
        example_transaction = """2024-05-06 ! "Transfer"
  Assets:Current-Assets:Checking-Account          -1000.0 EUR
  Assets:Current-Assets:Checkingaccount-Foo-Bank   1000.0 EUR
"""
        assert example_transaction in content

    def test_get_open_account_directives_creates_beancount_open_objects(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        gnucash_path = tmp_path / "gnucash.csv"
        gnucash_path.write_text(self.test_gnucash_csv)
        g2b = GnuCashCSV2Beancount(gnucash_path, Path(), config_path)
        g2b._prepare_csv()
        open_directives = g2b._get_open_account_directives()
        expected_account_openings = [
            data.Open(
                meta={"filename": PosixPath(gnucash_path), "lineno": 0},
                date=datetime.datetime(2024, 5, 1).date(),
                account="Assets:Current-Assets:Checking-Account",
                currencies=["EUR"],
                booking=data.Booking.FIFO,
            ),
            data.Open(
                meta={"filename": PosixPath(gnucash_path), "lineno": 1},
                date=datetime.datetime(2024, 5, 1).date(),
                account="Equity:Opening-Balances",
                currencies=["EUR"],
                booking=data.Booking.FIFO,
            ),
            data.Open(
                meta={"filename": PosixPath(gnucash_path), "lineno": 3},
                date=datetime.datetime(2024, 5, 3).date(),
                account="Expenses:Mygroceries",
                currencies=["EUR"],
                booking=data.Booking.FIFO,
            ),
            data.Open(
                meta={"filename": PosixPath(gnucash_path), "lineno": 7},
                date=datetime.datetime(2024, 5, 6).date(),
                account="Assets:Current-Assets:Checkingaccount-Foo-Bank",
                currencies=["EUR"],
                booking=data.Booking.FIFO,
            ),
            data.Open(
                meta={"filename": PosixPath(gnucash_path), "lineno": 5},
                date=datetime.datetime(2024, 5, 9).date(),
                account="Assets:Current-Assets:Wallet-Nzd",
                currencies=["NZD"],
                booking=data.Booking.FIFO,
            ),
        ]
        assert open_directives == expected_account_openings

    def test_get_transaction_directives_creates_beancount_transaction_objects(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        gnucash_path = tmp_path / "gnucash.csv"
        gnucash_path.write_text(self.test_gnucash_csv)
        g2b = GnuCashCSV2Beancount(gnucash_path, Path(), config_path)
        g2b._prepare_csv()
        transactions = g2b._get_transaction_directives()
        expected_transactions = [
            data.Transaction(
                meta={"filename": PosixPath(gnucash_path), "lineno": 0},
                date=datetime.datetime(2024, 5, 1).date(),
                flag="!",
                payee=None,
                narration="Opening",
                tags=frozenset(),
                links=set(),
                postings=[
                    data.Posting(
                        account="Assets:Current-Assets:Checking-Account",
                        units=amount.Amount(D("10000.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag=None,
                        meta=None,
                    ),
                    data.Posting(
                        account="Equity:Opening-Balances",
                        units=amount.Amount(D("-10000.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag=None,
                        meta=None,
                    ),
                ],
            ),
            data.Transaction(
                meta={"filename": PosixPath(gnucash_path), "lineno": 2},
                date=datetime.datetime(2024, 5, 3).date(),
                flag="!",
                payee=None,
                narration="Groceries",
                tags=frozenset(),
                links=set(),
                postings=[
                    data.Posting(
                        account="Assets:Current-Assets:Checking-Account",
                        units=amount.Amount(D("-120.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag=None,
                        meta=None,
                    ),
                    data.Posting(
                        account="Expenses:Mygroceries",
                        units=amount.Amount(D("120.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag=None,
                        meta=None,
                    ),
                ],
            ),
            data.Transaction(
                meta={"filename": PosixPath(gnucash_path), "lineno": 6},
                date=datetime.datetime(2024, 5, 6).date(),
                flag="!",
                payee=None,
                narration="Transfer",
                tags=frozenset(),
                links=set(),
                postings=[
                    data.Posting(
                        account="Assets:Current-Assets:Checking-Account",
                        units=amount.Amount(D("-1000.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag=None,
                        meta=None,
                    ),
                    data.Posting(
                        account="Assets:Current-Assets:Checkingaccount-Foo-Bank",
                        units=amount.Amount(D("1000.0"), currency="EUR"),
                        cost=None,
                        price=None,
                        flag=None,
                        meta=None,
                    ),
                ],
            ),
            data.Transaction(
                meta={"filename": PosixPath(gnucash_path), "lineno": 4},
                date=datetime.datetime(2024, 5, 9).date(),
                flag="!",
                payee=None,
                narration="MoneyTransfer",
                tags=frozenset(),
                links=set(),
                postings=[
                    data.Posting(
                        account="Assets:Current-Assets:Checking-Account",
                        units=amount.Amount(
                            D("-27.95"),
                            currency="EUR",
                        ),
                        cost=None,
                        price=amount.Amount(
                            D("1.7889087656529516490166997755295597016811370849609375"),
                            currency="NZD",
                        ),
                        flag=None,
                        meta=None,
                    ),
                    data.Posting(
                        account="Assets:Current-Assets:Wallet-Nzd",
                        units=amount.Amount(D("50.0"), currency="NZD"),
                        cost=None,
                        price=None,
                        flag=None,
                        meta=None,
                    ),
                ],
            ),
        ]
        assert transactions == expected_transactions

    def test_get_header_str_contains_options_and_plugins(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        gnucash_path = tmp_path / "gnucash.csv"
        gnucash_path.write_text(self.test_gnucash_csv)
        g2b = GnuCashCSV2Beancount(gnucash_path, Path(), config_path)
        header = g2b._get_header_str()
        expected_header = """plugin "beancount.plugins.auto"

option "operating_currency" "EUR"
option "title" "Exported GnuCash Book"

"""
        assert header == expected_header

    def test_get_commodities_contains_default_and_non_default_currencies(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        gnucash_path = tmp_path / "gnucash.csv"
        gnucash_path.write_text(self.test_gnucash_csv)
        g2b = GnuCashCSV2Beancount(gnucash_path, Path(), config_path)
        g2b._prepare_csv()
        commodities = g2b._get_commodities_str()
        expected_commodities = "2024-05-01 commodity EUR\n2024-05-01 commodity NZD\n\n"
        assert commodities == expected_commodities

    @mock.patch("g2b.g2b.parse_file")
    def test_verify_output_calls_beancount_parse_file(self, mock_parse, tmp_path):
        mock_parse.return_value = [[], [], {}]
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        gnucash_path = tmp_path / "gnucash.csv"
        gnucash_path.write_text(self.test_gnucash_csv)
        g2b = GnuCashCSV2Beancount(gnucash_path, Path(), config_path)
        g2b._verify_output()
        mock_parse.assert_called()

    @mock.patch("g2b.g2b.parse_file", mock.MagicMock(return_value=[[], [], {}]))
    @mock.patch("g2b.g2b.validate")
    def test_verify_output_calls_beancount_validate(self, mock_validate, tmp_path):
        mock_validate.return_value = {}
        config_path = tmp_path / "config.yaml"
        config_path.write_text(yaml.dump(self.test_config))
        gnucash_path = tmp_path / "gnucash.csv"
        gnucash_path.write_text(self.test_gnucash_csv)
        g2b = GnuCashCSV2Beancount(gnucash_path, Path(), config_path)
        g2b._verify_output()
        mock_validate.assert_called()
