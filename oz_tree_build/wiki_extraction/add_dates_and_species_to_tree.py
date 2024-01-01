"""
This script takes a tree in newick format and adds dates to the nodes based on
the Wikipedia fossil range data. It also adds a species name to each leaf node
based on the Wikipedia taxobox, if we don't already have a full species name.
"""

import argparse
import json
import logging
import sys
import dendropy
from oz_tree_build.utilities.debug_util import parse_args_and_add_logging_switch
from oz_tree_build.wiki_extraction.mwparserfromhell_helpers import (
    get_taxon_name,
    get_wikicode_for_page,
    get_display_string_from_wikicode,
    get_wikicode_template,
)


# Periods from https://en.wikipedia.org/wiki/Geologic_time_scale
PERIOD_LOOKUP = {
    "triassic": [251.902, 201.4],  # Period
    "induan": [251.902, 251.2],
    "olenekian": [251.2, 247.2],
    "anisian": [247.2, 242],
    "ladinian": [242, 237],
    "carnian": [237, 227],
    "norian": [227, 208.5],
    "rhaetian": [208.5, 201.4],
    "jurassic": [201.4, 145],  # Period
    "hettangian": [201.4, 199.5],
    "sinemurian": [199.5, 192.9],
    "pliensbachian": [192.9, 184.2],
    "toarcian": [184.2, 174.7],
    "aalenian": [174.7, 170.9],
    "bajocian": [170.9, 168.2],
    "bathonian": [168.2, 165.3],
    "callovian": [165.3, 161.5],
    "oxfordian": [161.5, 154.8],
    "kimmeridgian": [154.8, 149.2],
    "tithonian": [149.2, 145],
    "cretaceous": [145, 66],  # Period
    "early cretaceous": [145, 100.5],
    "late cretaceous": [100.5, 66],
    "berriasian": [145, 139.8],
    "valanginian": [139.8, 132.6],
    "hauterivian": [132.6, 125.77],
    "barremian": [125.77, 121.4],
    "aptian": [121.4, 113],
    "albian": [113, 100.5],
    "cenomanian": [100.5, 93.9],
    "turonian": [93.9, 89.8],
    "coniacian": [89.8, 86.3],
    "santonian": [86.3, 83.6],
    "campanian": [83.6, 72.1],
    "maastrichtian": [72.1, 66],
    "paleogene": [66, 23.03],
}


def get_node_taxon(node):
    if node.taxon:
        return node.taxon.label
    else:
        return node.label


def map_period_name_to_range(period_name):
    period_name = period_name.lower()
    if period_name in PERIOD_LOOKUP:
        return PERIOD_LOOKUP[period_name]

    logging.warning(f"Unknown period name: {period_name}")
    return None


def get_range_date(fossilrange_value, use_start):
    if isinstance(fossilrange_value, str):
        # If it's already a string, use it as is
        date = fossilrange_value
    else:
        periodstart_template = get_wikicode_template(fossilrange_value, ("periodstart"))
        if not periodstart_template:
            date = str(fossilrange_value)
        else:
            date = str(periodstart_template.params[0].value)

    # Convert it to a float if it looks like a number
    try:
        date = float(date)
    except ValueError:
        # If it's not a number, it could be a period name. If so, we grab
        # either the start or end date
        date_range = map_period_name_to_range(date)
        if not date_range:
            return None

        date = date_range[0] if use_start else date_range[1]

    return date


def get_date_range_from_taxobox(taxobox):
    range = taxobox.get("fossil_range").value
    # Template name can randomly be be "fossil range" or "geological range", with or without space/underscores
    fossil_range_template = get_wikicode_template(
        range, ("fossilrange", "geologicalrange")
    )

    if not fossil_range_template:
        range_string = get_display_string_from_wikicode(range)
        from_date = to_date = get_range_date(range_string, use_start=True)
    else:
        from_date = get_range_date(
            fossil_range_template.params[0].value, use_start=True
        )
        # If there is no end date, we fall back to the start date
        to_date = (
            get_range_date(fossil_range_template.params[1].value, use_start=False)
            if len(fossil_range_template.params) >= 2
            else from_date
        )

    return from_date, to_date


def get_species_from_taxobox(taxobox):
    if taxobox.has_param("type_species"):
        type_species = taxobox.get("type_species").value
        species_name = get_taxon_name(type_species)
    elif taxobox.has_param("genus") and taxobox.has_param("species"):
        genus = taxobox.get("genus").value
        species = taxobox.get("species").value
        species_name = get_taxon_name(genus) + " " + get_taxon_name(species)
    elif taxobox.has_param("taxon"):
        taxon = taxobox.get("taxon").value
        species_name = get_taxon_name(taxon)
    else:
        logging.warning(f"Could not find species name for {taxon}")
        return None

    # Make sure it's a binomial species name
    if not " " in species_name:
        logging.warning(
            f"For {taxon}, found '{species_name}' in taxobox, but it's not binomial"
        )
        return None

    return species_name


nodes_data = {}
taxon_to_page_mapping = {}


def get_taxon_data_from_wikipedia(taxon, is_leaf):
    logging.info(f"Processing taxon '{taxon}'")

    # If we have a mapping from taxon to page title, use that. This is
    # the case where the link display didn't match the link target
    if taxon in taxon_to_page_mapping:
        page_title = taxon_to_page_mapping[taxon]
    else:
        page_title = taxon

    # Get the Wikipedia page for the taxon
    wikicode = get_wikicode_for_page(page_title)
    if not wikicode:
        return None

    taxobox = get_wikicode_template(wikicode, ("automatictaxobox", "speciesbox"))
    if not taxobox:
        logging.warning(f"Could not find taxobox for {taxon}")
        return None

    node_data = {}
    from_date, to_date = get_date_range_from_taxobox(taxobox)
    if not from_date:
        logging.warning(f"Could not find fossil range for {taxon}")

    # Note that for species, the end date is the extinction date
    node_data["from_date"] = from_date
    node_data["to_date"] = to_date

    if is_leaf:
        # If the taxon in the newick is not a binomial species name
        if " " not in taxon:
            # Try to get the species name from the taxobox
            species_name = get_species_from_taxobox(taxobox)
            if species_name:
                node_data["species_name"] = species_name
            else:
                logging.warning(f"Could not find binomial species name for {taxon}")

    return node_data


def get_taxon_data_from_wikipedia_with_caching(taxon, is_leaf):
    if taxon in nodes_data:
        logging.info(f"Found cached data for {taxon}: '{nodes_data[taxon]}'")
        return nodes_data[taxon]

    nodes_data[taxon] = get_taxon_data_from_wikipedia(taxon, is_leaf)

    logging.info(f"{taxon}: '{nodes_data[taxon]}'")

    return nodes_data[taxon]


def process_leaf_node_and_get_extinction_date(node):
    # Find the node's taxon name
    if not node.taxon or not node.taxon.label:
        taxon = None
        logging.warning(f"Leaf node has no taxon: {node}")
        return 0

    taxon = node.taxon.label
    node_data = get_taxon_data_from_wikipedia_with_caching(taxon, is_leaf=True)

    if node_data and "to_date" in node_data:
        extinction_date = node_data["to_date"] or 0
    else:
        extinction_date = 0

    # If it's an extinct leaf, add a nameless prop-up node with the date
    if extinction_date:
        extinction_propup_node = dendropy.Node()
        extinction_propup_node.edge_length = extinction_date
        node.add_child(extinction_propup_node)

    return extinction_date


def process_interior_node_recursive_and_get_range(node):
    # Find the oldest 'from' date of all the children ranges
    oldest_child_from_date = 0
    child_with_oldest_from_date = None
    children_date_ranges = {}
    for child in node.child_nodes():
        child_date_range = process_node_recursive_and_get_range(child)
        if not child_date_range:
            continue
        if child_date_range[0]:
            if child_date_range[0] > oldest_child_from_date:
                oldest_child_from_date = child_date_range[0]
                child_with_oldest_from_date = child
        children_date_ranges[child] = child_date_range

    # Find the node's taxon name (interior nodes may not have a taxon)
    taxon = node.label

    # If we have a taxon name, get data from Wikipedia
    node_data = None
    if taxon:
        node_data = get_taxon_data_from_wikipedia_with_caching(taxon, node.is_leaf())

    if not node_data:
        node_data = {}

    if node_data and "from_date" in node_data and node_data["from_date"]:
        date_range = [node_data["from_date"], node_data["to_date"]]
        if oldest_child_from_date > date_range[0]:
            logging.warning(
                f"Node '{taxon}' has from_date {node_data['from_date']}, but its child {get_node_taxon(child_with_oldest_from_date)} has from_date {oldest_child_from_date}"
            )
            date_range[0] = oldest_child_from_date
    else:
        date_range = [oldest_child_from_date, 0]

    # Set the edge length for all the children
    for child in node.child_nodes():
        if child in children_date_ranges:
            child_date_range = children_date_ranges[child]
            child.edge_length = round(date_range[0] - child_date_range[0], 10)
            assert child.edge_length >= 0

    return date_range


def process_node_recursive_and_get_range(node):
    if node.is_leaf():
        extinction_date = process_leaf_node_and_get_extinction_date(node)

        # For a leaf, set the end date to the start date, since that's what we'll
        # want to use for the calculation of the edge length to its parent
        return [extinction_date, extinction_date]
    else:
        return process_interior_node_recursive_and_get_range(node)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "treefile",
        type=argparse.FileType("r"),
        nargs="?",
        default=sys.stdin,
        help="The tree file in newick form",
    )
    args = parse_args_and_add_logging_switch(parser)

    # Parse the input tree
    tree = dendropy.Tree.get(file=args.treefile, schema="newick")

    # Read the mapping from taxon to page title from the tree comments
    if tree.comments:
        taxon_to_page_mapping.update(json.loads(tree.comments[0]))

    # The taxon data cache file has the same name with a .json extension
    cache_filename = args.treefile.name + ".taxondatacache.json"
    try:
        with open(cache_filename) as f:
            nodes_data.update(json.load(f))
    except FileNotFoundError:
        pass

    process_node_recursive_and_get_range(tree.seed_node)

    # Print the updated tree
    print(tree.as_string(schema="newick"))

    # Save the taxon data cache file
    with open(cache_filename, "w") as f:
        json.dump(nodes_data, f)


if __name__ == "__main__":
    main()
