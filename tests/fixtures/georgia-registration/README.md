# Georgia SOS registration table definitions

`tables.json` projects the unchanged `GetProperties` responses for the public
monthly tables `CuucTJm` (demographics) and `dSyCvqD` (active/inactive pivot).
Retrieved November 1, 2026 from the public Qlik app
`2977350b-191a-4615-a761-664579f15891`, linked by
https://sos.ga.gov/election-data-hub through
https://d11khjvhu86vzz.cloudfront.net/DH.VOTERS/index.html.

Only public object definitions are included: no token responses, individual
voter rows or session credentials. The test engine supplies synthetic aggregate
rows. It exercises the actual source's pivot mode, calculated status dimension,
and date-specific measure expression rather than assuming both tables are
straight cubes with a simple sum.

Regression cases also cover Qlik dimension cells that omit qText without setting
qIsNull, observed in live municipal precinct aggregates. Missing text and explicit
null may be separate Qlik elements and must remain separate groups.
