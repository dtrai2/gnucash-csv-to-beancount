"""This module provides a converter that can translate a gnucash sql file into a beancount file"""

import datetime
import logging
import os.path
import re
from collections import defaultdict
from functools import cached_property
from pathlib import Path
from typing import Dict, List

import click
import piecash
import yaml
from beancount.core import data, amount
from beancount.core.number import D
from beancount.ops import validation
from beancount.ops.validation import validate
from beancount.parser import printer
from beancount.parser.parser import parse_file
from piecash._common import GnucashException
from rich.logging import RichHandler
from rich.progress import track

logging.basicConfig(
    level="NOTSET",
    format="%(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[RichHandler(omit_repeated_times=False)],
)

logger = logging.getLogger("g2b")


class G2BException(Exception):
    """Default Error for Exceptions"""


class GnuCash2Beancount:
    """Application to convert a gnucash sql file to a beancount ledger"""

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
        config = self._configs.get("beancount")
        switched_to_bean_event = {datetime.date.today(): "misc Changed from GnuCash to Beancount"}
        if "events" in config:
            config["events"].update(switched_to_bean_event)
        else:
            config["events"] = switched_to_bean_event
        return config

    @cached_property
    def _fava_config(self) -> Dict:
        return self._configs.get("fava", {})

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
        self._book = None
        self._output_path = output
        self._config_path = config
        self._commodities = defaultdict(list)
        logging.getLogger().setLevel(self._converter_config.get("loglevel", "INFO"))

    def _read_gnucash_book(self):
        """Reads the gnucash book"""
        try:
            self._book = piecash.open_book(
                os.path.abspath(str(self._filepath)), readonly=True, open_if_lock=True
            )
        except GnucashException as error:
            raise G2BException(
                f"File does not exist or wrong format exception: {error.args[0]}"
            ) from error

    def write_beancount_file(self) -> None:
        """
        Parse the gnucash file, read and convert everything such that a valid
        beancount ledger can be exported.
        """
        logger.info("Start converting GnuCash file to Beancount")
        logger.debug("Input file: %s", self._filepath)
        logger.debug("Config file: %s", self._config_path)
        logger.debug("Config: %s", self._configs)
        self._read_gnucash_book()
        transactions = self._get_transactions()
        openings = self._get_open_account_directives(transactions)
        events = self._get_event_directives()
        balance_statements = self._get_balance_directives()
        commodities = self._get_commodities()
        with open(self._output_path, "w", encoding="utf8") as file:
            printer.print_entries(
                commodities + openings + events + transactions + balance_statements,
                file=file,
                prefix=self._get_header_str(),
            )
        logger.info("Finished writing beancount file: '%s'", self._output_path)
        self._verify_output()

    def _get_transactions(self):
        transactions = []
        for transaction in track(self._book.transactions, description="Parsing Transactions"):
            skip_template = "Skipped transaction as it is malformed: %s"
            if len(transaction.splits) == 1 and transaction.splits[0].value == 0:
                logger.warning(skip_template, {transaction})
                continue
            if transaction.splits[0].account.commodity.mnemonic == "template":
                logger.warning(skip_template, {transaction})
                continue
            postings = self._get_postings(transaction.splits)
            posting_flags = [posting.flag for posting in postings]
            transaction_flag = "!" if "!" in posting_flags else "*"
            transactions.append(
                data.Transaction(
                    meta={"filename": self._filepath, "lineno": -1},
                    date=transaction.post_date,
                    flag=transaction_flag,
                    payee="",
                    narration=self._sanitize_description(transaction.description),
                    tags=data.EMPTY_SET,
                    links=set(),
                    postings=postings,
                )
            )
        transactions.sort(key=lambda txn: txn.date)
        return transactions

    def _get_postings(self, splits):
        postings = []
        for split in splits:
            account_name = str(self._apply_renaming_patterns(split.account.fullname))
            posting_currency = split.account.commodity.mnemonic.replace(" ", "")
            units = amount.Amount(number=split.quantity * D("1.0"), currency=posting_currency)
            not_reconciled_symbol = self._gnucash_config.get("not_reconciled_symbol")
            flag = "!" if not_reconciled_symbol in split.reconcile_state else "*"
            price = self._calculate_price_of_split(split)
            posting = data.Posting(
                account=account_name, units=units, cost=None, price=price, flag=flag, meta=None
            )
            self._commodities[posting_currency].append(split.transaction.post_date)
            self._commodities[posting_currency] = [min(self._commodities[posting_currency])]
            postings.append(posting)
        return postings

    def _calculate_price_of_split(self, split):
        if split.account.commodity == split.transaction.currency:
            return None
        currency = split.transaction.currency.mnemonic.replace(" ", "")
        if split.value == 0 and split.quantity == 0:
            return data.Amount(D("0"), currency)
        return data.Amount(abs(split.value / split.quantity), currency)

    def _get_event_directives(self) -> List[data.Event]:
        """Parse beancount configuration and create event directives"""
        events = []
        for date, event_description in self._bean_config.get("events", {}).items():
            event_type, description = event_description.split(" ", maxsplit=1)
            events.append(data.Event(date=date, type=event_type, description=description, meta={}))
        return events

    def _get_balance_directives(self) -> List[data.Balance]:
        balances = []
        default_currency = self._gnucash_config.get("default_currency")
        date_of_tomorrow = datetime.date.today() + datetime.timedelta(days=1)
        for account, balance_value in self._bean_config.get("balance-values", {}).items():
            currency = self._non_default_account_currencies.get(account, default_currency)
            balances.append(
                data.Balance(
                    date=date_of_tomorrow,
                    account=account,
                    amount=amount.Amount(number=D(str(balance_value)), currency=currency),
                    meta={},
                    tolerance=None,
                    diff_amount=None,
                )
            )
        return balances

    def _get_commodities(self):
        commodities = []
        for commodity, date in self._commodities.items():
            meta = {"filename": self._filepath, "lineno": -1}
            if self._fava_config.get("commodity-precision", None) is not None:
                meta.update({"precision": self._fava_config.get("commodity-precision")})
            commodities.append(data.Commodity(date=date[0], currency=commodity, meta=meta))
        return commodities

    def _apply_renaming_patterns(self, account_name):
        """
        Renames an account such that it complies with the required beancount format.
        The naming is also being capitalized here such that always only the first latter is written
        in capitals.
        """
        for pattern, replacement in self._account_rename_patterns:
            account_name = re.sub(pattern, repl=replacement, string=account_name)
        return account_name.title()

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

    def _verify_output(self) -> None:
        """
        Verifies the created beancount ledger by running the respective beancount parser and
        beancount validator. If any errors are found they are logged to the console.
        """
        logger.info("Verifying output file")
        entries, parsing_errors, options = parse_file(self._output_path)
        for error in parsing_errors:
            logger.error(error)
        validation_errors = validate(
            entries=entries,
            options_map=options,
            extra_validations=validation.HARDCORE_VALIDATIONS,
        )
        for error in validation_errors:
            logger.warning(error)
        if not parsing_errors and not validation_errors:
            logger.info("No parsing or validation errors found")
        if parsing_errors:
            logger.warning("Found %s parsing errors", len(parsing_errors))
        if validation_errors:
            logger.warning("Found %s validation errors", len(validation_errors))

    def _get_open_account_directives(self, transactions):
        account_date_tuples = [
            (posting.account, transaction.date, posting.units.currency)
            for transaction in transactions
            for posting in transaction.postings
        ]
        accounts = defaultdict(list)
        for account, date, currency in account_date_tuples:
            accounts[account].append((date, currency))
        openings = []
        for account, date_currency_tuples in accounts.items():
            dates, currencies = zip(*date_currency_tuples)
            openings.append(
                data.Open(
                    account=account,
                    currencies=[currencies[0]],
                    date=min(dates),
                    meta={"filename": self._filepath, "lineno": -1},
                    booking=None,
                )
            )
        return openings


@click.command()
@click.option(
    "--input",
    "-i",
    "input_path",
    type=click.Path(exists=True),
    help="Gnucash file path",
    required=True,
)
@click.option("--output", "-o", help="Output file path", required=True)
@click.option(
    "--config", "-c", help="Config file path", type=click.Path(exists=True), required=True
)
def main(input_path: Path, output: Path, config: Path) -> None:
    """
    GnuCash to Beancount Converter - g2b

    This tool allows you to convert a gnucash sql file into a new beancount ledger.
    """
    try:
        g2b = GnuCash2Beancount(input_path, output, config)
        g2b.write_beancount_file()
    except G2BException as error:
        logging.error(error)


if __name__ == "__main__":
    main()  # pylint: disable=no-value-for-parameter
