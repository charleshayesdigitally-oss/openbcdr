# Pending — out of scope

Files here are **not loaded**. `standards.load()` globs only the top level of
`standards/`, so moving an index file into this directory takes its records out
of scope entirely.

## Why you would use it

The report withholds its score while **any** requirement in scope is unvalidated.
With every source live, you are blocked on all of them before you can produce a
single scored report.

Move the sources you have not validated yet down here, validate one source, and
you get a real examiner-ready report off that source alone. Move each file back
as it is validated.

```
# take FFIEC and NIST out of scope
mv standards/ffiec-bcm-2019.json standards/pending/
mv standards/nist-800-34r1.json  standards/pending/

python -m openbcdr standards-status          # now reports only what is live
python -m openbcdr validate --file standards/finra-4370.json --validator YOUR_NAME
python -m openbcdr report --plan <PLAN> --examiner

# put a source back once its records are validated
mv standards/pending/ffiec-bcm-2019.json standards/
```

⚠️ **A file parked here is invisible, not deleted — and the report will not tell
you it is missing.** A narrower index produces a *higher-looking* coverage
percentage simply because fewer requirements were assessed. Whoever reads the
report needs to know which sources were in scope; the report header names them,
so read it.
