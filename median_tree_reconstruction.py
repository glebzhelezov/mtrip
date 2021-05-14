import re
import triplet_omp
import snoob
from multiprocessing import Pool
from bitsnbobs import popcount, get_binary_subsets, init_bipart_rep_function
from collections import deque, Counter
from itertools import product
from time import perf_counter
from functools import partial
from random import Random

# I know I shouldn't do this :(
# Only use multiprocessing for basic parsing if the list of nwks is quite long
__long_nwk_list__ = 100000

def simplify_nwk(s):
    """Returns Newick representation with only leaf names."""
    # Get rid of spaces at the start and end
    # This already takes place in median_triplet.py
    # s = s.strip()
    s = s.replace(" ", "")
    # Get rid of non-leaf names
    s = re.sub(r"\)[^,)]+", ")", s)
    # Get rid of branch lengths
    s = re.sub(r":[0-9]*[.]?[0-9]*", "", s)

    return s


def get_line_names(i, nwk):
    tokens = re.findall(r"([(,])([a-zA-Z0-9]*)(?::\d*(\.\d*)?)?(?=[,)])", nwk)
    cur_names = [t[1] for t in tokens]
    if "" in cur_names:
        raise SyntaxError(
            "Unlabeled tip in Newick string on line {}!".format(i + 1)
        )
    return cur_names


def get_names(gts_nwks, n_threads=1):
    """Gets the unique names in a list of Newick strings."""
    names = set([])

    if len(gts_nwks) > __long_nwk_list__:
        print("Many Newick strings, so doing this in parallel.")

        with Pool(n_threads) as p:
            for res in p.starmap(get_line_names, enumerate(gts_nwks)):
                names.update(res)
    else:
        for i, gt_nwk in enumerate(gts_nwks):
            cur_names = get_line_names(i, gt_nwk)
            names.update(cur_names)

    names_list = list(names)
    # names_list.sort()
    dictionary = {names_list[i]: i for i in range(len(names_list))}
    reverse_dictionary = names_list.copy()

    return names, dictionary, reverse_dictionary


def splitter(nwk):
    """Returns the two inner Newick subtrees; assumes Newick string does not
    end with a semicolon."""
    s = nwk
    bracket_count = 0
    start_position = 0

    if s[0] != "(":
        return tuple([s])

    for i, ch in enumerate(s):
        if ch == "(":
            bracket_count += 1
        elif ch == ")":
            bracket_count -= 1
        elif ch == ",":
            if bracket_count == 1:
                left = s[start_position + 1 : i]
                right = s[i + 1 : -1]

                return left, right


def get_biparts(nwk, dictionary):
    """Get all the bipartitions in the Newick tree. Each partition is
    represented by pairs of binary numbers. The smaller binary number is the
    first coordinate."""
    biparts = []

    def recurse(s):
        split = splitter(s)

        if len(split) == 1:
            return 2 ** dictionary[split[0]]
        if len(split) == 2:
            a = recurse(split[0])
            b = recurse(split[1])

            biparts.append((min(a, b), max(a, b)))
            return a + b

    # fill up the biparts list
    recurse(nwk)

    return biparts


def get_subset_biparts(nwks, dictionary):
    biparts_per_subset = dict([])

    def recurse(s):
        split = splitter(s)

        if len(split) == 1:
            return 2 ** dictionary[split[0]]
        if len(split) == 2:
            a = recurse(split[0])
            b = recurse(split[1])

            subset = a + b
            bipart = (min(a, b), max(a, b))

            # List this bipart as belonging to the partition
            if subset in biparts_per_subset:
                # print(subset)
                if bipart not in biparts_per_subset[subset]:
                    biparts_per_subset[subset].add(bipart)
            else:
                # print(subset)
                biparts_per_subset[subset] = set([bipart])

            return subset

    # fill up the biparts list
    for nwk in nwks:
        recurse(nwk)

    return biparts_per_subset

def _reducesets(bpses, k):
    """Returns [bps[k] for bps in bpses if k in bps.keys()]. Created only
    to overcome multiprocessing's limitations."""
    return [bps[k] for bps in bpses if k in bps.keys()]

def _get_subset_biparts_reversed_args(a,b):
    return get_subset_biparts(b,a)

def get_subset_biparts_parallel(nwks, dictionary, n_threads=1):
    if len(nwks) < 5*n_threads:
        # Don't bother if there's not many Newick strings
        return get_subset_biparts(nwks, dictionary)
    else:
        n_nwks = len(nwks)
        sublist_size = n_nwks//n_threads
        remainder = n_nwks % n_threads
        sublists = [nwks[i*sublist_size:(i+1)*sublist_size] for i in range(0, n_threads)]
        sublists[-1].extend(nwks[-remainder:])

        biparts_per_subset = dict([])
        with Pool(n_threads) as p:
            # Get the biparts per set in each subchunk of the data
            bpses = p.map(partial(_get_subset_biparts_reversed_args, dictionary), sublists)
            #bpses = p.starmap(get_subset_biparts, product(sublists, [dictionary]))
            # Get the keys
            keys = {k for bps in bpses for k in bps.keys()}
            bpses_individual = p.map(partial(_reducesets, bpses), keys)
            # bpses = [[bps[k] for bps in bpses if k in bps.keys()] for k in keys]
            full_bpses = p.starmap(set.union, bpses_individual)
            biparts_per_subset = {k:v for (k,v) in zip(keys, full_bpses)}

            # for bps in p.starmap(get_subset_biparts, product(sublists, [dictionary])):
            #         for key in bps.keys():
            #             if key in biparts_per_subset:
            #                 biparts_per_subset[key].update(bps[key])
            #             else:
            #                 biparts_per_subset[key] = set(bps[key])

        return biparts_per_subset

def get_weights(gts_nwks, dictionary):
    weights = {}
    for s in gts_nwks:
        biparts = get_biparts(s, dictionary)

        for bipart in biparts:
            if bipart in weights:
                weights[bipart] += 1
            else:
                weights[bipart] = 1

    return weights


def get_weights_parallel(gts_nwks, dictionary, n_threads=1):
    """Find the weights of the data biparts."""

    if len(gts_nwks) < 30*n_threads:
        return get_weights(gts_nwks, dictionary)

    all_biparts = deque()
    chunk = len(gts_nwks) // (n_threads * 10)

    with Pool(n_threads) as p:
        for res in p.starmap(
            get_biparts, product(gts_nwks, [dictionary]), chunksize=chunk,
        ):
            all_biparts.extend(res)

    return Counter(all_biparts)


def get_stack(bipartition_weights, n_species):
    print("* Finding maximal possible weight of each bipartition.")
    # The "stack" gives the best weight of each subset
    f = init_bipart_rep_function(n_species)
    # Score of each triple
    stack = triplet_omp.zero_array(2 ** n_species, "i")
    # stack = np.zeros(2 ** n_species, dtype=np.intc)
    # Each subset has a list of the maximizing bipartitions
    best_biparts = [[] for _ in range(2 ** n_species)]

    universe = 2 ** n_species - 1

    # Fill up the stack
    for n in range(3, n_species + 1):
        for combo in snoob.get_all_snoobs(universe, n):
            best_bipart_list = best_biparts[combo]
            max_score = -1
            for subset in get_binary_subsets(combo):
                complement = combo - subset
                if subset < complement:
                    score = (
                        bipartition_weights[f(subset, complement)]
                        + stack[subset]
                        + stack[complement]
                    )
                    if score == max_score:
                        best_bipart_list.append((subset, complement))
                    elif score > max_score:
                        max_score = score
                        best_bipart_list.clear()
                        best_bipart_list.append((subset, complement))
            # print('updating', combo)
            # best_bipart[combo] = max_bipart
            stack[combo] = max_score

    return stack, best_biparts


def process_nwks(nwks, n_threads=1, bufsize=3*10**7):
    """Returns weights of bipartitions, dictionary, and reverse dictionary
    
    Input:
    nwks - list of Newick strings
    n_threads - n threads to use (default=1)
    bufsize - chunk size with which to update the array (defaults to 3*10^6)"""
    print("* Parsing Newick strings and recording bipartitions in GTs.")
    # Get rid of unnecessary info in Newick string
    nwks_simplified = []
    if len(nwks) > __long_nwk_list__:
        print("Many Newick strings, so doing this in parallel.")
        with Pool(n_threads) as p:
            nwks_simplified.extend(p.map(simplify_nwk, nwks))
    else:
        nwks_simplified = [simplify_nwk(s) for s in nwks]
    # Map each name to an integer
    print("* Finding all unique names.")
    names, dictionary, reverse_dictionary = get_names(
        nwks_simplified, n_threads=n_threads
    )
    # Get the number of species across all the GTs
    n_species = len(names)
    # Warn user of impeding doom; this is a pretty low bar though, 20 is more
    # reasonable on modern hardware.
    if n_species > 18:
        print(
            "Warning: attempting to find exact tree with {} tips. The computation might run out of memory, or take an unreasonable amount of time.".format(
                n_species
            )
        )

    # Get the weights of the bipartitions in the GTs
    print("* Calculating each GT bipartition's weight.")
    #weights = get_weights(nwks_simplified, dictionary)
    weights = get_weights_parallel(
        nwks_simplified, dictionary, n_threads=n_threads
    )
    # Get the biparts by the subset (i.e. (a+b)->[(a,b),...]
    print("* Matching bipartitions to subsets.")
    biparts_by_subset = get_subset_biparts_parallel(nwks_simplified, dictionary, n_threads=n_threads)
    # Arrange data to be easily accessible by C code
    print("* Forming arrays for computations.")
    subsets = []
    start_i = []
    end_i = []
    biparts_a = []
    biparts_b = []
    bipart_weights = []

    # Permute the input to make the computations more uniform
    rng = Random(0)
    shuffled_keys = list(biparts_by_subset.keys())
    rng.shuffle(shuffled_keys)

    position = 0
    for subset in shuffled_keys:
        subsets.append(subset)
        start_i.append(position)
        biparts = biparts_by_subset[subset]
        for (a, b) in biparts:
            biparts_a.append(a)
            biparts_b.append(b)
            bipart_weights.append(weights[(a, b)])
            position += 1
        end_i.append(position)
    # Get the weights of all possible bipartitions
    print("* Finding each possible bipartition's weight:")
    triplet_weights = triplet_omp.py_compressed_weight_rep(
        subsets,
        start_i,
        end_i,
        biparts_a,
        biparts_b,
        bipart_weights,
        n_species,
        n_threads=n_threads,
        bufsize=bufsize,
    )
    print("Done!")

    return triplet_weights, dictionary, reverse_dictionary


def get_present_species(x, reverse_dictionary):
    # Can be optimized with bitwise operations
    return [
        reverse_dictionary[i]
        for i in range(len(reverse_dictionary))
        if x & (2 ** i) == 2 ** i
    ]


def _get_all_trees(x, dictionary, reverse_dictionary, best_biparts):
    all_trees = []
    if popcount(x) == 1:
        names = get_present_species(x, reverse_dictionary)
        all_trees.append(names[0])
    elif popcount(x) == 2:
        names = get_present_species(x, reverse_dictionary)
        all_trees.append("({},{})".format(*names))
    else:
        for (a, b) in best_biparts[x]:
            a_trees = _get_all_trees(
                a, dictionary, reverse_dictionary, best_biparts
            )
            b_trees = _get_all_trees(
                b, dictionary, reverse_dictionary, best_biparts
            )

            for a_prime in a_trees:
                for b_prime in b_trees:
                    all_trees.append("({},{})".format(a_prime, b_prime))

    return all_trees


def get_all_trees(x, dictionary, reverse_dictionary, best_biparts):
    return [
        t + ";"
        for t in _get_all_trees(x, dictionary, reverse_dictionary, best_biparts)
    ]


def median_triplet_trees(nwks, n_threads=1, bufsize=3*10**7):
    triplet_weights, dictionary, reverse_dictionary = process_nwks(
        nwks, n_threads=n_threads, bufsize=bufsize,
    )

    stack, best_biparts = get_stack(triplet_weights, len(dictionary))
    # bitset representation of all the tips
    x = 2 ** len(reverse_dictionary) - 1
    n = len(dictionary)
    # This assumes each GT has all the species, so this is actually not a
    # sharp upper bound!
    theoretical_bound = len(nwks) * n * (n - 1) * (n - 2) // 6
    print(
        "Best possible triplet count is {}, out of a maximum of {}.".format(
            stack[x], theoretical_bound
        )
    )
    return get_all_trees(x, dictionary, reverse_dictionary, best_biparts)
