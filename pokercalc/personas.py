"""Screen names and a short description of how each opponent plays, like the names you see at an online table.

The names are ordinary online handles and say nothing about how someone plays: each name is simply tied to one kind of player
(STYLE_OF), so the same name is always the same kind of player. The description is only shown once you have cashed out, and it is
only a summary of the style; each player still has their own slightly different numbers (bots.make_traits).
"""
import random

_NAMES = """joxxer DouchebagSteve xX_Kyle_Xx pokerface22 BigDaddyMike lukasz_k tacotuesday nightowl44 dave_1987 petrov77
sk8rboi mrmiyagi TheRealJamie hotdogjoe bluebirdandy ChrisP_UK Mariposa88 ginger_ninja coffeeaddict SirLoin
zack_attack flatwhite DJ_Marcus uncle_rico banana_hammock Tommy2Times carlos_rg peter_the_great gr8scott ollie.b
slimshady99 theBigLebowski rusty_nail nana_bear KingKev jorgen_s trucker_tom boardgamebob popcorn_pete sunny_side_up
MikeyMike AlexDeLarge pixelpusher cosmic_kate saltandvinegar fat_tony handyandy wafflehouse darkroast vinny_b
helsinki_hank norwegian_wood tuesday_tina gandalf_gray aaron_2k beer_garden jetpack88 lil_timmy sandwich_king pinkflamingo
bigmuscles buttercup_7 spaceman_spiff casper_gal tractor_boy jamjar thirsty_thursday kenny_loggins mojo_jojo bacon_bits
Greg_from_accounts the_dude_abides tex_mex sweet_caroline leeroy_j ricardo_m yorkshire_pud scouse_ste paddy_o stevie_g
nobody_special cheesy_pete rainy_day sofa_king jollyroger77 lord_of_the_wings count_chocula
mr_whiskers olive_juice pickle_rick lemon_drop midnight_snack tiny_dancer big_lou deano1979 Marzipan_Mike skipper_sue
OldSpiceDave el_gato_negro tuesdayjoe the_real_baz hairy_larry captain_crunch mr_bean_42 TinyTim_9 smoothie_steve ninja_nan
crispy_bacon jimbo_slice davey_boy toastie_tom kebab_king 2good4u neon_nights hector_the_hamster mrs_robinson ginger_pete
zorro_x denver_vern mountain_mick""".split()

_BLURBS = {
    "whale": [
        "Plays almost every hand and hates folding, with money that barely matters to them",
        "Raises big and often with weak hands, then pays off most bets",
        "Splashes chips into huge pots and never seems to care about the price",
        "Loves action: limps, raises and calls with hands that should be folded",
        "Chases every draw to the river and bets big when they hit",
        "Plays for the thrill, not the odds: loose, curious and generous",
        "Gets involved in nearly every pot and keeps firing, win or lose",
        "Throws in oversized bets with almost any hand",
        "Just got lucky and thinks poker is easy: loose and overconfident",
        "Re-raises light and refuses to fold to pressure",
        "Treats the table like a slot machine: pulls the trigger on anything",
        "Wants to impress: big bluffs, big calls and no discipline",
        "Out for a good time: raises blind and calls everything",
        "Sees every flop and pays off with weak pairs, often after betting big",
        "Deep pockets, loose style: stacks off with almost any piece of the board",
        "Flashy and impatient: 3-bets light and gets stubborn in big pots",
        "Used to winning elsewhere, so never believes you have it",
        "Treats every pot as a bet they must win and never folds a draw",
        "Plays lots of hands and calls raises on a whim, then bets big",
        "Bets the pot or more with hands that should check",
    ],
    "station": [
        "Calls too much and rarely raises: a bet from them usually means a real hand",
        "Hates folding a pair: you have to show a better hand",
        "Sees lots of flops, calls a lot, bluffs very little",
        "Hangs on to weak hands hoping for a miracle card",
        "Falls in love with any ace and will not let go",
        "Always wants to see one more card, so calls and calls",
        "Says they know the odds, but calls far too often anyway",
        "Will pay you off all the way down with second pair",
        "Cannot fold any suited hand, however weak",
        "Calls with every draw and every backdoor chance",
        "Folds about once an hour, usually by accident",
        "Treats top pair as a monster: calls three streets with it",
        "Stays in to the river just to see",
        "Calls to see what you have, every time",
        "Plays any pair as if it were the nuts, and never raises",
        "Limps in with everything and calls any raise cheaply",
        "Convinced you are bluffing: calls down far too light",
        "Loves a cheap flop and keeps calling once they have a piece",
        "Never met a gutshot they did not like to chase",
        "Calls and waits for a hand to show up, which pays you off",
    ],
    "maniac": [
        "Raises almost everything and fires on every street",
        "Wild and aggressive: lots of raises, lots of big bluffs",
        "Always the one putting in the next raise, strong or weak",
        "Unpredictable: 3-bets light and bets big for no reason",
        "Keeps betting until someone proves they have a hand",
        "Never stops betting: lots of overbets and re-raises",
        "Shoves far too often and calls shoves even more",
        "Explodes into big pots with whatever they are holding",
        "Plays furious: bigger raises after every losing hand",
        "Loud and aggressive: 3-bets, 4-bets, and keeps firing",
        "Gives chips away with a flood of bluffs",
        "Fast and wild: raises first, thinks later",
        "Loves a giant bet, with a hand or with nothing",
        "Attacks every pot and cannot stop raising",
        "Rolls over the table with constant aggression",
        "Fires barrel after barrel until called or out of chips",
        "Anything can happen: raises, bluffs and sudden shoves",
        "Raises preflop, bets every flop, and keeps going",
        "No pattern at all, just lots of aggression",
        "Plays their own game: light 3-bets and wild bluffs",
    ],
    "nit": [
        "Only plays premium hands and folds a lot: believe them when they bet",
        "Waits for the nuts and gives up easily without them",
        "Folds to any pressure and rarely bluffs",
        "Very tight and very passive until they pick up a monster",
        "Plays few hands and only bets big when they have it",
        "Waits for a pair or big ace; folds everything else",
        "Folds for hours then wakes up with aces",
        "Folds more than anyone at the table and still wins small pots",
        "Avoids trouble: folds to raises and rarely bluffs",
        "Solid and immovable but plays almost no hands",
        "Folds at the first sign of aggression",
        "Waits all night for a premium hand",
        "Hates losing chips so much they barely play",
        "Predictable: no bet without a strong hand",
        "Likes a safe fold, hates big pots without a monster",
        "Folds more than 85% of their hands",
        "Locks the chips away and only opens up for big hands",
        "Quiet for ages, then suddenly a big bet",
        "Plays AA, KK, QQ or AK, and little else",
        "When they raise they have it; when they do not, they fold",
    ],
    "tag": [
        "Plays a sensible tight-aggressive game with a few leaks",
        "Straightforward: raises good hands, folds the rest, bluffs now and then",
        "Cautious and tidy, but gives up too easily after the flop",
        "Decent amateur who knows the basics and overvalues top pair",
        "Plays for fun with a reasonable plan and the odd mistake",
        "Knows position matters and plays well in it, but is weak out of it",
        "Bets nearly every flop as the preflop raiser and gives up often",
        "Read a book and plays by the rules, but is too predictable",
        "Plays a reasonable game at weekends with some bad habits",
        "Bets good hands for value, but hardly ever bluffs",
        "Neat and sensible with ranges that are slightly too tight",
        "Careful with their stack and plays a safe, standard game",
        "Counts their outs and pot odds, but misreads the opponent",
        "Standard play, with a few predictable bluffs",
        "Rarely does anything crazy but folds to big raises",
        "Steals from the button a lot but is timid in the blinds",
        "Plays by the numbers they learned but struggles in new spots",
        "Takes their time and plays fairly, but is easy to push around",
        "Wins at their home game and plays a solid, slightly exploitable style",
        "Plays right down the middle with no big leaks and no big edge",
    ],
    "reg": [
        "Calm and precise: sharp ranges, well-timed bluffs, hard to read",
        "Studies every spot: balanced bets and good calls, almost no leaks",
        "Tight and aggressive in position, punishes any weakness",
        "Plays the numbers: right sizes, right frequencies, rarely pays you off",
        "Tireless and takes thin edges all day without tilting",
        "Fearless bluffer and hero-caller who adjusts to you fast",
        "Smells weakness: attacks checks and folds, hard to bluff",
        "Plays close to solver strategy: mixed bets, perfect frequencies",
        "Sharp and efficient: no wasted chips and no mistakes to exploit",
        "Never tilts, never tires, plays the same strong game for hours",
        "Hunts the weakest player and takes their stack",
        "Quiet and invisible, then suddenly you are out of chips",
        "Tight, aggressive and impossible to run over",
        "Adjusts every orbit to the way you play",
        "Patient: waits, reads, then strikes",
        "Strikes with perfectly timed raises and cold bluffs",
        "Always one step ahead: balanced and almost unreadable",
        "Disciplined and fearless, folds when beaten and bets when ahead",
        "Finds the thin value bet and the thin bluff catch every time",
        "Plays at the top of their game and lets nobody off the hook",
    ],
}

_ORDER = ["whale", "station", "maniac", "nit", "tag", "reg"]
_shuffled = _NAMES[:]
random.Random(2024).shuffle(_shuffled)                      # a fixed shuffle: no pattern in which names belong to which kind of player
POOLS = {style: list(zip(_shuffled[i * 20:(i + 1) * 20], _BLURBS[style])) for i, style in enumerate(_ORDER)}
SHARKS = [n for n, _ in POOLS["reg"]]          # the strongest opponents; there is an achievement for beating each one
STYLE_OF = {name: style for style, pool in POOLS.items() for name, _ in pool}      # a name always belongs to the same kind of player


def pick(style, rng, taken=()):
    """{'name', 'blurb'} for a player of this style, not using a name already at the table."""
    free = [p for p in POOLS[style] if p[0] not in taken]
    if not free:
        n = len(POOLS[style]) + 1
        name, blurb = rng.choice(POOLS[style])
        return {"name": f"{name}{n}", "blurb": blurb}
    name, blurb = rng.choice(free)
    return {"name": name, "blurb": blurb}
