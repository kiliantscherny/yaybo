"""Everything that reads tinglysning.dk, the Danish land register.

    client     the HTTP session against the register, public and secured
    address    resolving what a person typed, via DAWA, to something askable
    attest     the logged-in record, read into the columns a row can hold
    attest_xml the whole of that record, taken apart into its own tables
    historik   the block of free text the register prints for a past transfer
    rows       the shapes all of the above end up in
    fields     the register's three dialects of date and number, normalised
"""
