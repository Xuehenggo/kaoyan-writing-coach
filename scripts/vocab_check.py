"""Check if words are within the 5500 vocab boundary. Loads once, answers instantly.

Usage:
    python vocab_check.py <word> [<word2> ...]     # Quick single-word check
    python vocab_check.py --text "<sentence>"        # Check all words in a sentence
    python vocab_check.py --file <path>               # Check all words in a file

For --text mode: extracts English words, checks each, flags out-of-bound words
with similar-word suggestions from the 5500 list.

The full word list (references/vocab-5500-full.md) is loaded once and cached.
"""

import re
import sys
import os
import json
from difflib import get_close_matches

if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

WORD_LIST_PATH = os.path.join(os.path.dirname(__file__), '..', 'references', 'vocab-5500-full.md')
_vocab_set = None
_vocab_list = None

# Zero-level words — too basic for the "默写版" word list but unquestionably
# within the 5500 boundary. Covers articles, common verbs, prepositions,
# conjunctions, pronouns, demonstratives, numbers, and common nouns/adjectives.
_ZERO_LEVEL = {
    'a', 'an', 'the', 'this', 'that', 'these', 'those',
    'some', 'any', 'no', 'all', 'each', 'every', 'both',
    'either', 'neither', 'few', 'many', 'much', 'several',
    'be', 'am', 'is', 'are', 'was', 'were', 'been', 'being',
    'have', 'has', 'had', 'having',
    'do', 'does', 'did', 'done', 'doing',
    'make', 'made', 'making',
    'get', 'got', 'getting',
    'say', 'said', 'saying',
    'go', 'went', 'gone', 'going',
    'come', 'came', 'coming',
    'take', 'took', 'taken', 'taking',
    'know', 'knew', 'known', 'knowing',
    'see', 'saw', 'seen', 'seeing',
    'think', 'thought', 'thinking',
    'give', 'gave', 'given', 'giving',
    'find', 'found', 'finding',
    'tell', 'told', 'telling',
    'use', 'used', 'using',
    'put', 'putting', 'set', 'setting', 'let', 'letting',
    'can', 'could', 'may', 'might', 'shall', 'should',
    'will', 'would', 'must', 'need', 'dare',
    'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from',
    'into', 'onto', 'upon', 'over', 'under', 'above', 'below',
    'between', 'among', 'through', 'throughout', 'across',
    'about', 'against', 'around', 'behind', 'beside',
    'near', 'off', 'out', 'toward', 'towards', 'within',
    'without', 'after', 'before', 'since', 'until', 'during',
    'except', 'like', 'as', 'than', 'up', 'down',
    'and', 'but', 'or', 'nor', 'yet', 'so', 'for',
    'because', 'although', 'though', 'while', 'when',
    'if', 'unless', 'once', 'where', 'whereas', 'whether',
    'i', 'you', 'he', 'she', 'it', 'we', 'they',
    'me', 'him', 'her', 'us', 'them',
    'my', 'your', 'his', 'its', 'our', 'their',
    'mine', 'yours', 'hers', 'ours', 'theirs',
    'myself', 'yourself', 'himself', 'herself', 'itself',
    'ourselves', 'yourselves', 'themselves',
    'who', 'whom', 'whose', 'which', 'what',
    'whoever', 'whatever', 'whichever',
    'good', 'better', 'best', 'bad', 'worse', 'worst',
    'big', 'larger', 'largest', 'small', 'little', 'less', 'least',
    'new', 'old', 'young',
    'first', 'second', 'third', 'last', 'next',
    'same', 'different', 'other', 'another',
    'great', 'high', 'low', 'long', 'short',
    'right', 'wrong', 'true', 'false',
    'full', 'empty', 'open', 'closed',
    'easy', 'hard', 'fast', 'slow',
    'chinese', 'american', 'british', 'japanese', 'french',
    'german', 'italian', 'russian', 'australian',
    'english', 'indian', 'korean', 'spanish',
    'time', 'year', 'day', 'night', 'week', 'month',
    'world', 'country', 'city', 'place', 'home',
    'people', 'person', 'man', 'woman', 'child', 'children',
    'name', 'life', 'way', 'thing', 'part', 'end',
    'hand', 'eye', 'head',
    'earth', 'sun', 'moon', 'water', 'fire',
    'love', 'hope', 'dream', 'work', 'play',
    'not', 'so', 'very', 'too', 'also', 'just',
    'now', 'then', 'here', 'there', 'where',
    'always', 'never', 'often', 'sometimes', 'usually',
    'well', 'only', 'even', 'still', 'already',
    'today', 'tomorrow', 'yesterday',
    'again', 'always', 'almost',
    'far', 'near', 'ever',
    'one', 'two', 'three', 'four', 'five', 'six', 'seven',
    'eight', 'nine', 'ten', 'hundred', 'thousand', 'million',
    'why', 'how', 'what', 'when', 'where',
    'yes', 'no', 'not',
    'very', 'much', 'more', 'most',
    'such', 'quite', 'rather',
    'ago', 'away', 'back',
    'too', 'very',
    # Common irregular past / participle forms
    'brought', 'thought', 'bought', 'fought', 'caught', 'taught',
    'built', 'sent', 'meant', 'felt', 'kept', 'slept',
    'spent', 'bent', 'lent',
    'grew', 'knew', 'threw', 'drew', 'flew', 'blew',
    'wore', 'swore', 'tore', 'bore', 'spoke', 'broke', 'woke',
    'chose', 'froze', 'drove', 'rode', 'wrote', 'rose',
    'ate', 'fell', 'took', 'shook', 'gave', 'forgave',
    'sang', 'sank', 'rang', 'drank', 'swam', 'began', 'ran',
    'won', 'hung', 'stuck', 'struck', 'lit',
    'lost', 'left', 'led', 'laid', 'lied', 'hid', 'slid',
    'bit', 'quit', 'cast', 'broadcast', 'burst', 'cost',
    'spread', 'hit', 'hurt', 'let', 'put', 'set', 'shut',
    'begun', 'hidden', 'forgiven', 'ridden', 'written', 'forbidden',
    'spoken', 'broken', 'taken', 'shaken', 'woken', 'frozen', 'chosen',
    'driven', 'risen', 'given', 'eaten', 'fallen', 'forgotten',
    'sung', 'sunk', 'rung', 'drunk', 'swum', 'begun',
}


def load_vocab():
    """Load 5500 vocab. Cached after first call."""
    global _vocab_set, _vocab_list
    if _vocab_set is not None:
        return _vocab_set

    _vocab_set = set(_ZERO_LEVEL)
    _vocab_list = list(_ZERO_LEVEL)
    for alt in [
        os.path.normpath(WORD_LIST_PATH),
        os.path.normpath(os.path.join(
            os.path.dirname(__file__), '..', '..', 'references', 'vocab-5500-full.md')),
    ]:
        if os.path.exists(alt):
            with open(alt, 'r', encoding='utf-8') as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith('- '):
                        word = stripped[2:].strip().lower()
                        _vocab_set.add(word)
                        _vocab_list.append(word)
            return _vocab_set
    print("ERROR: Word list not found", file=sys.stderr)
    return _vocab_set


def suggest_similar(word, n=3):
    if not _vocab_list:
        return []
    return get_close_matches(word.lower(), _vocab_list, n=n, cutoff=0.7)


def try_demorph(word):
    """Find the base form of an inflected word. Returns matching base or None."""
    w = word.lower()
    if len(w) <= 2:
        return None
    vocab = load_vocab()
    if w in vocab:
        return w
    candidates = []

    # Suffix rules
    if w.endswith('ies') and len(w) > 3:
        candidates.append(w[:-3] + 'y')
    if w.endswith('ves') and len(w) > 3:
        candidates.append(w[:-3] + 'fe')
    if w.endswith('ses'): candidates.append(w[:-3] + 's')
    if w.endswith('zes'): candidates.append(w[:-3] + 'z')
    if w.endswith('ches'): candidates.append(w[:-4] + 'ch')
    if w.endswith('shes'): candidates.append(w[:-4] + 'sh')
    if w.endswith('xes'): candidates.append(w[:-3] + 'x')
    if w.endswith('sses'): candidates.append(w[:-4] + 'ss')
    if w.endswith('s') and not w.endswith('ss') and len(w) > 3:
        candidates.append(w[:-1])
    if w.endswith('ied'): candidates.append(w[:-3] + 'y')
    if w.endswith('pped'): candidates.append(w[:-3])
    if w.endswith('tted'): candidates.append(w[:-3])
    if w.endswith('nned'): candidates.append(w[:-3])
    if w.endswith('gged'): candidates.append(w[:-3])
    if w.endswith('rred'): candidates.append(w[:-3])
    if w.endswith('lled'): candidates.append(w[:-3])
    if w.endswith('ed') and len(w) > 4:
        candidates.append(w[:-2])
        candidates.append(w[:-2] + 'e')
    if w.endswith('ying'): candidates.append(w[:-4] + 'ie')
    if w.endswith('tting') or w.endswith('nning') or w.endswith('pping') \
            or w.endswith('rring') or w.endswith('lling'):
        candidates.append(w[:-4])
    if w.endswith('ing') and len(w) > 5:
        candidates.append(w[:-3])
        candidates.append(w[:-3] + 'e')
    if w.endswith('ier'): candidates.append(w[:-3] + 'y')
    if w.endswith('iest'): candidates.append(w[:-4] + 'y')
    if w.endswith('er') and len(w) > 3:
        candidates.append(w[:-2])
        candidates.append(w[:-2] + 'e')
    if w.endswith('est') and len(w) > 4:
        candidates.append(w[:-3])
        candidates.append(w[:-3] + 'e')
    if w.endswith('ily'): candidates.append(w[:-3] + 'y')
    if w.endswith('bly'): candidates.append(w[:-3] + 'ble')
    if w.endswith('ly') and len(w) > 3: candidates.append(w[:-2])
    if w.endswith('ation'): candidates.append(w[:-5] + 'ate')
    if w.endswith('ication'): candidates.append(w[:-7] + 'y')
    if w.endswith('tion'):
        candidates.append(w[:-4] + 't')
        candidates.append(w[:-4])
    if w.endswith('sion'): candidates.append(w[:-4] + 'de')
    if w.endswith('ment'): candidates.append(w[:-4])
    if w.endswith('ness'):
        candidates.append(w[:-4] + 'y')
        candidates.append(w[:-4])
    if w.endswith('ity'):
        candidates.append(w[:-3] + 'e')
        candidates.append(w[:-3] + 'ous')
        candidates.append(w[:-3])
    if w.endswith('able'):
        candidates.append(w[:-4])
        candidates.append(w[:-4] + 'ate')
    if w.endswith('ible'): candidates.append(w[:-4])
    if w.endswith('ive'):
        candidates.append(w[:-3] + 'e')
        candidates.append(w[:-3])
    # -al suffix (common adjective form)
    if w.endswith('tional'):
        candidates.append(w[:-5] + 'tion')  # traditional -> tradition
    if w.endswith('ional'):
        candidates.append(w[:-5] + 'ion')   # educational -> education
    if w.endswith('ental'):
        candidates.append(w[:-4] + 'ent')   # environmental -> environment
    if w.endswith('mental'):
        candidates.append(w[:-5] + 'ment')  # governmental -> government
    if w.endswith('tural'):
        candidates.append(w[:-4] + 'ture')  # cultural -> culture
    if w.endswith('ral'):
        candidates.append(w[:-3] + 're')    # cultural -> culture (alt)
    if w.endswith('ical'):
        candidates.append(w[:-4] + 'y')
        candidates.append(w[:-4] + 'ic')
    if w.endswith('al'):
        candidates.append(w[:-2])           # general -al removal
    if w.endswith('ous'): candidates.append(w[:-3])
    if w.endswith('ful'): candidates.append(w[:-3])
    if w.endswith('less'): candidates.append(w[:-4])
    if w.endswith('ish'): candidates.append(w[:-3])

    # Prefix stripping (after suffix)
    prefixes = ['un', 'in', 'im', 'ir', 'dis', 're', 'over']
    for pfx in prefixes:
        if w.startswith(pfx) and len(w) > len(pfx) + 2:
            stripped = w[len(pfx):]
            candidates.append(stripped)
            if stripped.endswith('ed'): candidates.append(stripped[:-1])
            if stripped.endswith('ing'): candidates.append(stripped[:-3])
            if stripped.endswith('s') and not stripped.endswith('ss'):
                candidates.append(stripped[:-1])

    for c in candidates:
        if c in vocab and len(c) >= 2:
            return c
    return None


def check_word(word):
    """Returns (is_in, suggestion_or_none, base_word_or_none)."""
    w = word.strip().lower()
    if len(w) <= 1:
        return (True, None, None)
    vocab = load_vocab()
    if w in vocab:
        return (True, None, w)
    base = try_demorph(w)
    if base and base in vocab:
        return (True, None, base)
    similar = suggest_similar(w, n=3)
    if similar:
        return (False, "Outside 5500; similar: " + ", ".join(similar), None)
    return (False, "Outside 5500; replace with in-boundary word", None)


def check_text(text):
    """Extract English words from text and check each."""
    tokens = re.findall(r"[a-zA-Z]{2,}", text)
    results = []
    seen = set()
    out_of_bound = []
    for w in tokens:
        w_lower = w.lower()
        if w_lower in seen:
            continue
        seen.add(w_lower)
        in_bound, suggestion, base = check_word(w)
        if not in_bound:
            out_of_bound.append(w)
        results.append((w, in_bound, suggestion, base))
    return results, out_of_bound


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return
    if args[0] == '--text':
        text = ' '.join(args[1:]) if len(args) > 1 else sys.stdin.read()
        results, out = check_text(text)
        if out:
            print("  OUT ({}) : {}".format(len(out), ', '.join(out)))
        else:
            print("  OK : all words within 5500 boundary")
        print()
        for word, in_bound, suggestion, base in results:
            if in_bound:
                detail = " (base: {})".format(base) if base and base != word.lower() else ""
                print("  OK   {}{}".format(word, detail))
            else:
                print("  OUT  {}  -- {}".format(word, suggestion))
    elif args[0] == '--file':
        if len(args) < 2:
            print("Usage: vocab_check.py --file <path>")
            return
        with open(args[1], 'r', encoding='utf-8') as f:
            text = f.read()
        results, out = check_text(text)
        if out:
            print("  OUT ({}) : {}".format(len(out), ', '.join(out)))
            report = {
                'file': args[1],
                'total_unique_words': len(results),
                'out_of_bound': [{'word': w, 'suggestion': s} for w, _, s, _ in results if s]
            }
            report_path = args[1] + '.vocab-report.json'
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print("  Report: {}".format(report_path))
        else:
            print("  OK : all words within 5500 boundary")
    else:
        for word in args:
            in_bound, suggestion, base = check_word(word)
            if in_bound:
                detail = " (base: {})".format(base) if base and base != word.lower() else ""
                print("  OK   {}{}".format(word, detail))
            else:
                print("  OUT  {}  -- {}".format(word, suggestion))


if __name__ == '__main__':
    main()
