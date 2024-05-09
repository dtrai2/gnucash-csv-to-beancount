"""This module provides a converter that can translate a gnucash csv export into a beancount file"""

import logging
import re
from functools import cached_property
from pathlib import Path
from typing import Dict, List

import click
import pandas as pd
import yaml
from beancount.core import data, amount
from beancount.core.number import D
from beancount.ops import validation
from beancount.ops.validation import validate
from beancount.parser import printer
from beancount.parser.parser import parse_file
from rich.logging import RichHandler
from rich.progress import track
from simpleeval import simple_eval

logging.basicConfig(
    level="NOTSET",
    format="%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[RichHandler(omit_repeated_times=False)],
)


class G2BException(Exception):
    """Default Error for Exceptions"""


class GnuCashCSV2Beancount:
    """Application to convert a gnucash csv export to a beancount ledger"""

    _DEFAULT_ACCOUNT_RENAME_PATTERNS = [
        (r"\s", "-"),
        ("_", "-"),
        (r"\.$", ""),
        (r"\.", "-"),
        ("&", "-"),
        (r"\(", ""),
        (r"\)", ""),
        ("---", "-"),
    ]
    """Pattern for character replacements in account names"""

    _CSV_COLUMN_NAMES = [
        "Date",
        "BookingID",
        "Number",
        "Description",
        "Remark",
        "Commodity",
        "CancellationReason",
        "Action",
        "BookingText",
        "FullAccountName",
        "AccountName",
        "ValueWithSymbol",
        "ValueNumerical",
        "ValueWithSymbol2",
        "ValueNumerical2",
        "Reconciliation",
        "ReconciliationDate",
        "Rate",
    ]
    """List of column names that will be applied to the csv export"""

    @cached_property
    def _configs(self) -> Dict:
        """Loads and returns the configuration as a dict"""
        with open(self._config_path, "r", encoding="utf8") as file:
            try:
                return yaml.safe_load(file)
            except yaml.YAMLError as error:
                raise G2BException("Error while parsing config file") from error

    @cached_property
    def _converter_config(self) -> Dict:
        """Returns configurations only related to the converter itself"""
        return self._configs.get("converter")

    @cached_property
    def _gnucash_config(self) -> Dict:
        """Returns configurations only related to gnucash"""
        return self._configs.get("gnucash")

    @cached_property
    def _bean_config(self) -> Dict:
        """Returns configurations only related to the beancount export"""
        return self._configs.get("beancount")

    @cached_property
    def _account_rename_patterns(self) -> List:
        """Returns a list of pattern that should be used to sanitize account names"""
        return (
            self._gnucash_config.get("account_rename_patterns", [])
            + self._DEFAULT_ACCOUNT_RENAME_PATTERNS
        )

    @cached_property
    def _non_default_account_currencies(self) -> Dict:
        """Returns a list of account currency mappings for non default accounts"""
        return self._gnucash_config.get("non_default_account_currencies", {})

    def __init__(self, filepath: Path, output: Path, config: Path):
        self._filepath = filepath
        self._output_path = output
        self._config_path = config
        self._dataframe = None
        self._logger = logging.getLogger("g2b")
        self._logger.setLevel(self._converter_config.get("loglevel", "INFO"))

    def write_beancount_file(self) -> None:
        """
        Parse the configuration file, read the csv export and convert everything such that a valid
        beancount ledger can be exported.
        """
        self._logger.info("Start converting GnuCash CSV File to Beancount")
        self._logger.debug("Input file: %s", self._filepath)
        self._logger.debug("Config file: %s", self._config_path)
        self._logger.debug("Config: %s", self._configs)
        self._prepare_csv()
        openings = self._get_open_account_directives()
        transactions = self._get_transaction_directives()
        with open(self._output_path, "w", encoding="utf8") as file:
            file.write(self._get_header_str())
            file.write(self._get_commodities_str())
            printer.print_entries(openings + transactions, file=file)
        self._logger.info("Finished writing beancount file: '%s'", self._output_path)
        self._verify_output()

    def _prepare_csv(self) -> None:
        """Sanitizes the gnucash export"""
        self._logger.info("Preparing dataframe")
        self._dataframe = pd.read_csv(self._filepath)
        self._dataframe.columns = self._CSV_COLUMN_NAMES
        self._dataframe["FullAccountName"] = self._dataframe["FullAccountName"].apply(
            self._apply_renaming_patterns
        )
        thousands_symbol = self._gnucash_config.get("thousands_symbol", ",")
        decimal_symbol = self._gnucash_config.get("decimal_symbol", ".")
        for col in ["Rate", "ValueNumerical"]:
            self._dataframe[col] = self._dataframe[col].str.replace(thousands_symbol, "")
            self._dataframe[col] = self._dataframe[col].str.replace(decimal_symbol, ".")
        self._dataframe["Rate"] = self._dataframe["Rate"].apply(simple_eval)
        for col in ["Rate", "ValueNumerical"]:
            self._dataframe[col] = pd.to_numeric(self._dataframe[col])
        self._dataframe["Date"] = pd.to_datetime(self._dataframe["Date"], format="%d.%m.%Y")
        self._dataframe.sort_values(by="Date", inplace=True)

    def _apply_renaming_patterns(self, account_name):
        """
        Renames an account such that it complies with the required beancount format.
        The naming is also being capitalized here such that always only the first latter is written
        in capitals.
        """
        for pattern, replacement in self._account_rename_patterns:
            account_name = re.sub(pattern, repl=replacement, string=account_name)
        return account_name.title()

    def _get_open_account_directives(self) -> List[data.Open]:
        """
        Gets a list of unique account names and their corresponding date where they first appeared.

        :returns: A list of beancount open directives
        """
        dataframe = self._dataframe.filter(items=["Date", "FullAccountName"])
        dataframe.drop_duplicates(subset=["FullAccountName"], inplace=True)
        openings = []
        for index, row in track(
            dataframe.iterrows(), total=len(dataframe), description="Parsing Account Openings..."
        ):
            currency = self._non_default_account_currencies.get(
                row["FullAccountName"], self._gnucash_config.get("default_currency")
            )
            openings.append(
                data.Open(
                    meta={"filename": self._filepath, "lineno": index},
                    date=row["Date"].date(),
                    account=row["FullAccountName"],
                    currencies=[currency],
                    booking=data.Booking.FIFO,
                )
            )
        return openings

    def _get_transaction_directives(self) -> List[data.Transaction]:
        """
        Groups every entry inside the gnucash export by the respective BookingID.
        For each group a transaction object with all corresponding postings is created and returned.

        :return: List of beancount transactions
        """
        entries = []
        groups = self._dataframe.groupby(by="BookingID")
        for _, group in track(groups, description="Parsing Transactions..."):
            postings = self._get_transaction_postings(group)
            transaction = self._get_transaction(group, postings)
            entries.append(transaction)
        entries = sorted(entries, key=lambda x: x.date)
        return entries

    def _get_transaction_postings(self, transaction_group) -> List[data.Posting]:
        """
        Returns a list of beancount postings that reflect one transaction.

        :param transaction_group: One transaction grouped by the gnucash BookingID
        :return: List of postings that are part of this transaction
        """
        postings = []
        default_currency = self._gnucash_config.get("default_currency")
        for _, row in transaction_group.iterrows():
            currency = self._non_default_account_currencies.get(
                row["FullAccountName"], default_currency
            )
            # need to convert numerical value back to string as it would have otherwise the
            # imperfections of float, e.g 2.2 could become 2.2000000000000000001234312312334
            unit = amount.Amount(D(str(row["ValueNumerical"])), currency=currency)
            price = float(row["Rate"]) if float(row["Rate"]) != 1.0 else None
            if price is not None:
                price = self._get_price_of_posting(price, currency, transaction_group)
            postings.append(
                data.Posting(
                    account=row["FullAccountName"],
                    units=unit,
                    cost=None,
                    price=price,
                    flag=None,
                    meta=None,
                )
            )
        return postings

    def _get_price_of_posting(self, price, unit_currency, transaction_group) -> amount.Amount:
        """
        Gets the price, and it's corresponding currency of the transaction.
        Assigns the default currency if the unit_currency differs from it. If the default and
        unit currencies are equal though, then the gnucash export had an issue. If the transaction
        has only two postings with two separate currencies it takes the currency of the other
        transaction, such that beancount can estimate the correct price for it later.

        :param price: The price/conversion number of the currency
        :param unit_currency: The currency of the transaction itself
        :param transaction_group: The dataframe group containing all postings
        :return: The amount.Amount of the price of this transaction
        """
        default_currency = self._gnucash_config.get("default_currency")
        price_currency = default_currency
        account_currencies = [
            self._non_default_account_currencies.get(row["FullAccountName"], default_currency)
            for _, row in transaction_group.iterrows()
        ]
        if unit_currency == default_currency and len(set(account_currencies)) == 2:
            currency_set = set(account_currencies)
            price_currency = currency_set.difference({unit_currency}).pop()
        price = amount.Amount(D(price), currency=price_currency)
        return price

    def _get_transaction(self, transaction_group, postings) -> data.Transaction:
        """
        Returns a beancount Transaction object by combining the transaction meta information
        with the previously created postings.

        :param transaction_group: The gnucash dataframe group
        :param postings: The beancount postings that are part of this transaction
        :return:
        """
        unique_descriptions = transaction_group["Description"].unique()
        if len(unique_descriptions) > 1:
            self._logger.warning(
                "More than one description found for a transaction: %s, "
                "using only first description: '%s'",
                unique_descriptions,
                transaction_group["Description"].iloc[0],
            )
        reconciliations = transaction_group["Reconciliation"].values
        # if one symbol is not reconciled mark all as not reconciled with !
        flag = "!" if self._gnucash_config.get("not_reconciled_symbol") in reconciliations else "*"
        transaction = data.Transaction(
            meta={"filename": self._filepath, "lineno": transaction_group.index[0]},
            date=transaction_group["Date"].iloc[0].date(),
            flag=flag,
            payee=None,
            narration=self._sanitize_description(transaction_group["Description"].iloc[0]),
            tags=data.EMPTY_SET,
            links=set(),
            postings=postings,
        )
        return transaction

    def _sanitize_description(self, description) -> str:
        """Removes unwanted characters from a transaction narration"""
        description = description.replace("\xad", "")
        description = description.replace('"', "'")
        return description

    def _get_header_str(self) -> str:
        """Returns a string that combines the configured beancount options and plugins"""
        plugins = [f'plugin "{plugin}"' for plugin in self._bean_config.get("plugins")]
        options = [f'option "{key}" "{value}"' for key, value in self._bean_config.get("options")]
        header = "\n".join(plugins + [""] + options)
        return f"{header}\n\n"

    def _get_commodities_str(self) -> str:
        """
        Returns a string with the commodities, combined from the default currency and the configured
        non default currencies.
        """
        earliest_date = self._dataframe["Date"].iloc[0].date()
        default_currency = [
            f"{earliest_date} commodity {self._gnucash_config.get('default_currency')}"
        ]
        commodities_strs = [
            f"{earliest_date} commodity {commodity}"
            for commodity in self._non_default_account_currencies.values()
        ]
        joined_str = "\n".join(default_currency + commodities_strs)
        return f"{joined_str}\n\n"

    def _verify_output(self) -> None:
        """
        Verifies the created beancount ledger by running the respective beancount parser and
        beancount validator. If any errors are found they are logged to the console.
        """
        self._logger.info("Verifying output file")
        entries, parsing_errors, options = parse_file(self._output_path)
        for error in parsing_errors:
            self._logger.error(error)
        validation_errors = validate(
            entries=entries,
            options_map=options,
            extra_validations=validation.HARDCORE_VALIDATIONS,
        )
        for error in validation_errors:
            self._logger.warning(error)
        if not parsing_errors and not validation_errors:
            self._logger.info("No parsing or validation errors found")
        if parsing_errors:
            self._logger.warning("Found %s parsing errors", len(parsing_errors))
        if validation_errors:
            self._logger.warning("Found %s validation errors", len(validation_errors))


@click.command()
@click.option(
    "--input",
    "-i",
    "input_path",
    type=click.Path(exists=True),
    help="Gnucash CSV file path",
    required=True,
)
@click.option("--output", "-o", help="Output file path", required=True)
@click.option(
    "--config", "-c", help="Config file path", type=click.Path(exists=True), required=True
)
def main(input_path: Path, output: Path, config: Path) -> None:
    """
    GnuCash CSV to Beancount Converter - g2b

    This tool allows you to convert a gnucash csv export into a new beancount ledger.
    """
    g2b = GnuCashCSV2Beancount(input_path, output, config)
    g2b.write_beancount_file()


if __name__ == "__main__":
    main()  # pylint: disable=no-value-for-parameter)
