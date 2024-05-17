# Gnucash to Beancount

This project can convert a [Gnucash](https://github.com/Gnucash/gnucash) sql file into a new
[beancount](https://github.com/beancount/beancount) file.
It is not intended to continuously import gnucash data into an existing beancount ledger, as this 
script will also add plugins and beancount options to the beginning of the file.
This project started with the intention to convert a gnucash csv export, but it turned out that the
csv exported from gnucash is not quite reliable.
Read more about that at in the section [Unreliable Export](#unreliable-gnucash-csv-export).
Because of that I refactored it to use the [piecash](https://pypi.org/project/piecash/) library.
With that it has the same goal as the already existing repository from
[henriquebastos/gnucash-to-beancount](https://github.com/henriquebastos/gnucash-to-beancount).
The implementation in this repository does offer few configuration options though.

One downside I have encountered sofar are stock splits. 
Those are currently not supported and have to be added manually to the output by following the
official documentation
[Beancount Stock Splits](https://beancount.github.io/docs/trading_with_beancount.html#stock-splits).
Luckily those splits don't happen too often.

## Prerequisite

Your gnucash file must be in a sql format.
I implemented it with a sqlite3 file, which was saved by GnuCash v5.4.
If your current genucash file is not in the right format it is always possible to just save it 
as a sqlite3 file.

## Install

To install `gnucash to beancount` simply use `pip`:

```bash
pip install g2b
```

Test with ``g2b --version`` if the installation was successful. 

## Usage

### Create Configuration for g2b

In order for a successful conversion you need to create a `yaml` configuration file.
An example would look like this:

```yaml
converter:
  loglevel: INFO
gnucash:  # here you can specify details about your gnucash export
  default_currency: EUR
  thousands_symbol: "."
  decimal_symbol: ","
  reconciled_symbol: "b"
  not_reconciled_symbol: "n"
  account_rename_patterns:  # Here you can rename accounts that might not align with the beancount format
    - ["OpenBalance", "Equity:Opening-Balance"]
    - ["Money@[Bank]", "Assets:Money at Bank"]
  non_default_account_currencies:  # Here you have to name all accounts that deviate from the default currency
    Assets:Cash:Wallet: "NZD"
beancount:  # here you can add beancount options, plugins and events that should be added to output file
  options:
    - ["title", "Exported GnuCash Book"]  # options should be key value pairs
    - ["operating_currency", "EUR"]
  plugins:
    - "beancount.plugins.check_commodity"  # plugins can be named directly
    - "beancount.plugins.coherent_cost"
    - "beancount.plugins.nounused"
    - "beancount.plugins.auto"
  events:
    2024-05-05: type description string  # optional events that should be added to the output, the
                                         # first space is used to split between space and description
```

## Execute g2b

Once you created the needed configuration file you can call:

```bash
g2b -i book.gnucash -c config.yaml -o my.beancount
```

The script will, at the end, automatically call beancount to parse and verify the export, such
that you know if the conversion was successful or not.

## Limitations

Currently, this project can not deal with stock splits.
Those will not be added to the beancount output and have to be added manually.
To do that follow the official
[Documentation](https://beancount.github.io/docs/trading_with_beancount.html#stock-splits).

## Unreliable Gnucash CSV Export

While starting out with CSV exports I found the following issues that kept me from
progressing with the CSV exports.

- The gnucash csv export does not offer reliable currency information.
  Transaction with values like `100 $` cannot be properly understood as it, for example, could be
  USD or NZD.
  An offical bug report is open since 2017:
  [gnucash bug - use ISO 4217 currency symbols in output](https://bugs.gnucash.org/show_bug.cgi?id=791651).
- The `Rate/Price` value is sometimes added to the wrong posting in transactions with multiple 
  currencies.
  Because of that it wasn't easily recognizable how to convert which prices.
- And probably the most severe issue: Some transactions were completely missing inside the export.
  As I couldn't figure out why I decided to use the `piecash` library. 
