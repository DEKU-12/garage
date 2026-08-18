<!-- Week 2. The ponytail ladder lives here (PRD §5.3), checked in order:
       1. Does this code need to exist at all? (Is the fix a deletion?)
       2. Does the codebase already contain this?
       3. Does the standard library provide this?
       4. Does a native platform feature provide this?
       5. Does an already-installed dependency provide this?
       6. Can it be one line?

     Out: `ACCEPT`  or  `REJECT / rung <1-6> / <one paragraph>`
     Parsed with a strict regex. A malformed verdict is treated as ACCEPT and
     logged as parse_warning -- never crash on model text (rules.md §3.1).

     Open question Q2: diff-only, or diff + surrounding context? Decide week 2. -->
