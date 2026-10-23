# Datasets

Raw datasets are never committed. Download them into `data/raw/` (gitignored).

## DataCo Smart Supply Chain for Big Data Analysis

- Source: Mendeley Data, <https://doi.org/10.17632/8gx2fvg2k6.5>
- Authors: Fabian Constante, Fernando Silva, António Pereira (2019)
- License: CC BY 4.0
- Files used: `DataCoSupplyChainDataset.csv` (and `DescriptionDataCoSupplyChain.csv` for column descriptions)
- Place them in `data/raw/`.

Content: order-level outbound data (order date, shipping date, real vs. scheduled
shipping days, delivery status, shipping mode, customer and order geography).
There is no inbound or inventory data.

Privacy: the CSV contains customer names, street addresses and masked e-mails and
passwords. The SC LiveSim adapter does not import these fields; customers get
pseudonymous IDs.
