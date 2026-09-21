"""What the plan will not tell a customer to do.

Observing a pattern and issuing an instruction are different acts. The account read may say
"the files with a man on camera got 18% more link clicks" -- that is a measured fact about
this account's own creative, and it belongs in the evidence. A brief saying "cast a man" is
the product directing an advertiser to select people by a protected characteristic, in a
surface built to be handed to a production team.

So these dimensions stay everywhere they are today -- the graded tests, the account read, the
ask box, the evidence lines a brief cites -- and are barred from the two places the plan
prescribes rather than reports: the ingredients of a Make brief, and the Explore list of
values to go and try.

Not on the list, deliberately: `cast` (solo person, couple, child, pet). Casting a couple is
an ordinary creative decision, not a demographic selection.

`NOT_EXPLORABLE` is a second, narrower list, and it exists because the rendered page made the
problem obvious: Explore was telling a US supplement brand it had "never tried" fr, de, pt, it
and hi. Shooting a Hindi ad is a market-entry decision, not a creative test, and an account
that has never run one has not left an opportunity on the table. These stay available to Make,
where graded evidence has to argue for them first; they are only barred from the block that
proposes things purely because they are absent.
"""

from __future__ import annotations

NOT_PRESCRIBED = frozenset(
    {
        "messenger_age_band",
        "messenger_gender_presentation",
        "demographic_signal",
    }
)


# Creative choices the team does not make by picking a different shot or line.
NOT_EXPLORABLE = frozenset({"spoken_language", "subtitle_language"})


def prescribable(dimension: str) -> bool:
    return dimension not in NOT_PRESCRIBED


def explorable(dimension: str) -> bool:
    return prescribable(dimension) and dimension not in NOT_EXPLORABLE
