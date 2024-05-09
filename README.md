# Gnucash CSV to Beancount

This project can convert a [Gnucash](https://github.com/Gnucash/gnucash) CSV Export into a new
[beancount](https://github.com/beancount/beancount) file.
It is not intended to continuously import gnucash data into an existing beancount ledger, as this 
script will also add plugins and beancount options to the beginning of the file.

## Install

To install `gnucash csv to beancount` simply use `pip`:

```bash
pip install g2b
```

## Usage

### Create a GnuCash Export

Start the export by navigating to `File > Export > Export Transactions to CSV`. 
You can follow the official gnucash [Export Transactions](https://www.gnucash.org/docs/v4/C/gnucash-help/trans-export.html)
Documentation.
Consider the following points while configuring the export:

- Use the comma seperator
- Check the option `Use Qoutes`
- **Do not** use the simple layout

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
beancount:  # here you can add beancount options and plugins that should be added to output file
  options:
    - ["title", "Exported GnuCash Book"]  # options should be key value pairs
    - ["operating_currency", "EUR"]
  plugins:
    - "beancount.plugins.check_commodity"  # plugins can be named directly
    - "beancount.plugins.coherent_cost"
    - "beancount.plugins.nounused"
    - "beancount.plugins.auto"
```

## Execute g2b

Now that you have the gnucash export and the corresponding configuration file you can call:

```bash
g2b -i gnuchash.csv -c config.yaml -o my.beancount
```

The script will automatically call beancount to parse and verify the export, such that you know
if the conversion was successful or not.

## Limitations

The conversion sadly doesn't work perfectly when it comes to transactions with multiple currencies,
or currency conversions. 
This is in part due to the gnucash export itself.
The column `Commodity/Currency` doesn't truly reflect the currency of the transaction.
Furthermore, the column `Ammount with Symbol` has ambiguous symbols as it doesn't use the ISO-4217
Currency codes. 
With that it is for example not clear if `100 $` are USD or NZD. 
A change was already proposed back in 2017:
[gnucash bug - use ISO 4217 currency symbols in output](https://bugs.gnucash.org/show_bug.cgi?id=791651).

To work a bit around that you have to specify currencies inside the configuration file for your accounts,
that deviate from the default currency.
After the conversion it is still possible though that beancount will complain about transactions
with multiple currency. 
That is also because gnucash assigns the `Rate/Price` inside the export to the wrong account.
For example a ledger (with default currency EUR) has a transaction from an NZD account to an EUR
account.
Transactions that were exported correctly will appear like this:

| Date         | FullAccountName        | Amount Num | Rate/Price |
|--------------|------------------------|------------|------------|
| 2024-05-09   | Assets:Wallet(NZD)     | 200 $      | 0.56       |
| 2024-05-09   | Expense:Groceries(EUR) | 111.74 €   | 1.00       |

The non-default currency account `Assets:Wallet(NZD)` has here a `Rate/Price` of `0.56`, whereas
the other position has a value of `1.00`.

In some cases the export has an entry like this though:

| Date         | FullAccountName        | Amount Num | Rate/Price |
|--------------|------------------------|------------|------------|
| 2024-05-09   | Assets:Wallet(NZD)     | 200 $      | 1.00       |
| 2024-05-09   | Expense:Groceries(EUR) | 111.74 €   | 0.56       |

Where the `Rate/Price` of `0.56` is assigned to the account with the default currenyc.
That leads to a currency conversion from EUR to EUR, which results in problems in beancount.
Transactions that only consist of two postings should be fine after a small change. 
If the transaction has multiple postings though you have to fix them manually. 
Luckily beancount will tell you with errors/warnings where you have to look to fix them.

It is also possible to add the beancount option `inferred_tolerance_default`
(see [Beancount Options](https://beancount.github.io/docs/beancount_options_reference.html)) to
specify a tolerance for certain currencies.
This is merely a way to mute the warnings though as it doesn't fix the problem, it just tells
beancount to be less strict. 
