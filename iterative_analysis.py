# -*- coding: utf-8 -*-
"""
A post-processing pipeline for PanOCT which attempts to account for microsynteny loss between
otherwise-independent syntenic gene clusters with a high degree of reciprocal sequence similarity.

Requirements:
    - Python (written for 2.7.x)
        - BioPython (1.70)

    - PanOCT (>3.23)

    - R (>3.0)
        - Cairo (>1.5)
        - UpSetR (>1.3)
        - ggplot2
        - ggrepel

    - MacOS (tested on MacOS >10.12) or Linux (tested on SLES 11).

Recent changes:

    v0.1.6 (March 2018)
    - Removed gap boolean from gap_finder, not necessary?

    v0.1.5 (March 2018)
    - Improved cluster merging.
    - Made gap_finder code MUCH easier to read.
    - Implemented cluster top hit reciprocity option (default 1.0).
    - Integrated R scripts into pipeline.

    v0.1.4 (March 2018)
    - Implementing full cluster merging (as opposed to n-1 + 1 merging).
    - Improved logging.

    v0.1.3 (March 2018)
    - Added R scripts.

    v0.1.2 (January 2018)
    - Added parallelized BLASTp searches via multiprocessing.Pool.
    - Made parallel_BLAST code easier to read.

    v0.1.1 (Winter 2017)
    - Completely rewritten to integrate with gene prediction pipeline.

    v0.1.0 (Autumn 2017)
    - Initial test version based on GFF files taken from NCBI, AUGUSTUS, &c.

To-do:

    - Improve CLI.
    - Write replacement orthology search.
    - Convert some of the more maths-y parts to Cython if feasible.
    - Lookahead for R being installed (if not, just print commands to log files)?

Written by Charley McCarthy, Genome Evolution Lab, Department of Biology,
Maynooth University in 2017-2018 (Charley.McCarthy@nuim.ie).
"""

from __future__ import division

import datetime
import multiprocessing as mp
import os
import subprocess as sp
import time
from collections import Counter
from csv import reader
from glob import glob

from Bio import SearchIO, SeqIO
from panpipes.Tools import flatten, grouper, seq_ratio
from panpipes.Tools import merge_clusters
from panpipes.Tools import subject_top_hit, query_top_hit, query_hit_dict, subject_hit_dict

##### Here is the function to handle individual BLASTp searches in parallel_BLAST. #####
def subprocess_BLAST(cmd):
    """
    Run an individual instance of a parallel_BLAST search.
    """
    subblastlog.write("Running {0}".format(" ".join(cmd)) + "\n")
    sp.call(cmd)


##### Here are the major functions used in cluster_clean. #####
def parallel_BLAST(list_of_genes, seqindex, split_by, out):
    """
    Run a BLASTp all-vs.-all search on a subset of genes from a database.

    Splits queries into separate files by a divisor split_by, BLASTs them
    simultaneously and then concatenates them using cat. Currently uses
    subprocess over BioPython's BLASTp wrapper for easy parallelization
    using mp.Pool and subprocess_BLAST. Returns a SearchIO BLAST tabular object.

    Requires cat, makeblastdb and blastp in your $PATH!

    Arguments:
        list_of_genes = List of (remaining) noncore protein IDs.
        seqindex      = SeqIO.index of all proteins.
        split_by      = Divisor to split files into.
        out           = File prefix for results files given current cluster size being investigated.
    """

    ##### Generate FASTA database of (remaining) noncore proteins. #####
    outfast = open(out, "w")
    for seq in list_of_genes:
        outfast.write(">{0}\n{1}\n".format(seqindex[seq].id, seqindex[seq].seq))
    outfast.close()  # Close file here, makeblastdb has problems otherwise.
    subblastlog.write("Wrote {0} sequences to {1}...\n".format(len(list_of_genes), out))

    ##### Run makeblastdb on FASTA database. #####
    sp.call(["makeblastdb", "-in", out, "-dbtype", "prot", "-out", "{0}.db".format(out)], stdout=subblastlog)

    ##### Split FASTA database by split_by and generate list of BLASTp commands. #####
    count = 0
    query_cmds = []  # Handle for simultaneous BLASTp commands.
    to_split = SeqIO.parse("{0}".format(out), "fasta")
    for part in grouper(to_split, int(round(len(list_of_genes) / split_by))):
        count = count + 1
        seqs = filter(lambda x: x is not None, part)  # Remove fill values.
        if glob("{0}.part{1}.faa".format(out, count)):  # Remove previous versions of file if present.
            os.remove("{0}.part{1}.faa".format(out, count))
        SeqIO.write(seqs, "{0}.part{1}.faa".format(out, count), "fasta")
        query_cmds.append(["blastp", "-query", "{0}.part{1}.faa".format(out, count),
                           "-db", "{0}.db".format(out), "-outfmt", "6 std qlen slen", "-evalue",
                           "0.0001", "-out",
                           "{0}.part{1}.subblast".format(out, count),
                           "-num_threads", "1"])
    subblastlog.write("Split original query file {0} ({1} sequences) into {2} files.\n".format(out, len(list_of_genes),
                                                                                               str(split_by)))

    ##### Run BLASTp processes simultaneously using mp.Pool. #####
    farm = mp.Pool(processes=split_by)
    farm.map(subprocess_BLAST, query_cmds)
    farm.close()
    subblastlog.write(
        "Finished BLAST+ searches for {0} ({1} sequences), split into {2} files.\n".format(out, len(list_of_genes),
                                                                                           str(split_by)))

    ##### Concatenate parallel_BLAST results together in the shell and remove other files. #####
    sp.call(["cat"] + glob("*subblast"), stdout=open("{0}.results".format(out), "wb"))
    for bin_file in glob("%s.db*" % out):
        os.remove(bin_file)
    for part_file in glob("%s.part*" % out):
        os.remove(part_file)
    os.remove("%s" % out)
    subblastlog.write(
        "Concatenated BLAST+ output for {0} ({1} sequences).\n".format(out, len(list_of_genes), str(split_by)))

    ##### Parse parallel_BLAST results and return them as a SearchIO.index instance to cluster_clean. #####
    blast = SearchIO.index("{0}.results".format(out), "blast-tab", fields=blast_fields)
    subblastlog.write("Loaded {0} as SearchIO.index for cluster_clean.\n".format(out))
    return blast


def gap_finder(blast_results, seqindex, noncore, total, current, min_id_cutoff, strain_cutoff):
    """
    Find potential "gaps" in noncore clusters arising from microsynteny loss.

    Workflow:
        1.  Loop through each noncore cluster.
        2.  Ignore clusters whose number of members is not the current size
            under investigation.
        3.  Generate dictionary of all BLAST hit IDs for every member of a cluster over the identity cutoff.
        4a. Loop through each "query" cluster in the BLAST hits dictionary.
        4b. If no homolog has already been found, loop through the hits of a given member of the cluster.
        4c. If one of the hits is from a strain missing from or not already added to the current cluster.
        4d. Calculate sequence length ratio between member protein and hit.
        4e. Check that hit is present in >strain_cutoff members of the query cluster.
        4f. If it is, get that hit protein's associated cluster and check that its size is not greater than total - current.
        5a. If "hit" cluster is a singleton, check if every member of the "query" cluster is (first) a BLASTp hit and
            (second) the top BLASTp hit for their respective strains for the "hit" cluster protein.
        5b. If so, add that "hit" cluster to a list of suitable "fill" clusters for the "query" cluster.
        6a. If "hit" cluster is not a singleton, check that every member of the "hit" cluster are also subjects
            of every member of the "query" cluster (e.g. if "hit" cluster has two proteins, and "query" cluster
            has 4 proteins, each member of the "query" cluster must have the two "hit" cluster proteins as a BLASTp
            hit such that the number of times the "hit" cluster proteins are present in the BLAST hit dictionary
            for the "query" cluster is equal to 8).
        6b. Check that every strain represented in the "hit" cluster is missing from the "query" cluster.
        6c. Check that every member of the "query" cluster is (first) a BLASTp hit and
            (second) the top BLASTp hit for their respective strains for each "hit" cluster protein.
        6d. If so, add that "hit" cluster to a list of suitable "fill" clusters for the "query" cluster.


    Returns a dictionary of "query" clusters whose values are "subject" clusters which pass these criteria.

    Arguments:
        blast_results = All-vs.-all noncore proteins BLASTp results.
        seqindex      = SeqIO.index of all proteins.
        noncore       = Noncore cluster dictionary.
        total         = Total number of genomes.
        current       = Cluster size being queried.
        min_id_cutoff = Percentage identity of a BLASTp hit (default = 30).
        strain_cutoff = Cutoff fraction of reciprocal strain top hits between two clusters (default = 1).
    """
    homologs = {}
    for cluster in noncore:  # Loop through non-core clusters.
        found = []  # Default for strains "added" to cluster.
        members = filter(lambda x: x != "----------", noncore[cluster])  # Get actual cluster.
        if len(members) != current:
            pass  # Ignore clusters outside of the current size.
        else:  # If cluster size = current.
            query_cluster_length = len(members)
            blast_hit_dict = query_hit_dict(members, blast_results, min_id_cutoff)
            for key in blast_hit_dict:  # Loop through each protein in the "query cluster".
                for hit in blast_hit_dict[key]:  # Loop through every hit from a given query cluster protein.
                    strain_tag = hit.split("|")[0]
                    if strain_tag not in [i.split("|")[0] for i in blast_hit_dict]:
                        if strain_tag not in found:
                            if seq_ratio(seqindex, key, hit) >= 0.6:
                                singlehitcount = len(filter(lambda x: x == hit, flatten(blast_hit_dict.values())))
                                if singlehitcount / query_cluster_length >= strain_cutoff:
                                    if subject_top_hit(blast_hit_dict.values(), hit, query_cluster_length, strain_cutoff):
                                        for subject_cluster in noncore:
                                            if hit in noncore[subject_cluster]:
                                                subject_cluster_length = len(filter(lambda x: x != "----------", noncore[subject_cluster]))
                                                if total >= query_cluster_length + subject_cluster_length:  # If the size of the subject cluster is < the number of missing strains from the query cluster.
                                                    subjhits = subject_hit_dict(noncore[subject_cluster], blast_results, min_id_cutoff)
                                                    subshitinquery = len(filter(lambda x: x in noncore[subject_cluster], set(flatten(blast_hit_dict.values()))))
                                                    if subject_cluster_length > 1:  # If the subject cluster is not a singleton cluster.
                                                        if subshitinquery / subject_cluster_length >= strain_cutoff:
                                                            strains_in_subject = [i.split("|")[0] for i in filter(lambda x: x != "----------", noncore[subject_cluster])]  # Strains present in the subject cluster.
                                                            if not filter(lambda x: x in strains_in_subject, [i.split("|")[0] for i in blast_hit_dict]):  # If all strains present in the subject cluster are missing from the query cluster.
                                                                reciphitcount = len(filter(lambda x: x in blast_hit_dict, set(flatten(subjhits.values()))))
                                                                if reciphitcount / query_cluster_length >= strain_cutoff:
                                                                    strains_in_query = [key.split("|")[0] for key in blast_hit_dict]
                                                                    if query_top_hit(blast_hit_dict, strains_in_query, subjhits.values(), subject_cluster_length, strain_cutoff):
                                                                        for s in strains_in_subject:
                                                                            found.append(s)
                                                                        if cluster in homologs:  # Allow more than one cluster to be associated.
                                                                            homologs[cluster].append(subject_cluster)
                                                                        else:
                                                                            homologs[cluster] = [subject_cluster]
                                                    else:
                                                        if subshitinquery / subject_cluster_length >= strain_cutoff:  # If every member of the query cluster is also a subject of the protein in the singleton subject cluster.
                                                            strains_in_query = [key.split("|")[0] for key in blast_hit_dict]
                                                            if query_top_hit(blast_hit_dict, strains_in_query,
                                                                             subjhits.values(), query_cluster_length,
                                                                             strain_cutoff):
                                                                found.append(hit.split("|")[
                                                                                 0])  # Shortcut: append hit ID substring because we're only looking at a singleton.
                                                                if cluster in homologs:  # Allow more than one subject cluster to be associated to a query cluster.
                                                                    homologs[cluster].append(subject_cluster)
                                                                else:
                                                                    homologs[cluster] = [subject_cluster]
    return homologs

def cluster_clean(panoct_clusters, fasta_handle, split_by=4, min_id_cutoff=30, strain_cutoff=1.0, iterations=1):
    """
    Tidy up non-core clusters found by PanOCT.

    Feeds into parallel_BLAST, which expects you to have BLAST+ installed.
    Also feeds into gap_finder, which doesn't require anything else.
    """
    ##### Load in FASTA database and PanOCT results. #####
    db = SeqIO.index(fasta_handle, "fasta")
    full_blast = SearchIO.index("blast_results.txt", "blast-tab")
    matchtable = reader(open(panoct_clusters), delimiter="\t")

    ##### Initialize empty dictionaries for cluster types. #####
    core = {}
    noncore = {}
    softcore = {}

    ##### Initialize variables for total/starting number of genomes. #####
    total = 0
    start = 0

    ##### Populate core & noncore dictionaries by reading PanOCT results. #####
    for row in matchtable:
        if "----------" in row:
            noncore[row[0]] = row[1:]  # Populating our initial noncore dict.
        else:
            core[row[0]] = row[1:]  # Populating our core dict.
            if total == 0:
                total = len(row) - 1  # Total number of genomes.
                print total
                start = total - 1  # Max noncore cluster size.

    mainlogfile.write(
        "{0} core clusters and {1} noncore clusters identified...\n".format(len(core), len(noncore)))

    #### Run parallel_BLAST and gap finding for n iterations. #####
    for iteration in range(0, iterations, 1):
        mainlogfile.write("Running iteration {0}...\n".format(iteration + 1))
        ##### Loop through noncore clusters from size (total -1) to 2. #####
        for size in range(start, 0, -1):
            filled_count = 0
            merged_count = 0
    
            ##### Get list of (remaining) noncore protein IDs. #####
            to_blast = filter(lambda x: x != "----------", flatten([noncore[key] for key in noncore]))
            mainlogfile.write("All-vs.-all BLAST of {0} proteins...\n".format(len(to_blast)))
    
            ##### Run parallel_BLAST. #####
            results = parallel_BLAST(to_blast, db, split_by, "ClusterBLAST_{0}.fasta".format(str(size)))
            outfast = open("ClusterBLAST_{0}.fasta".format(str(size)), "w")
            for seq in to_blast:
                outfast.write(">{0}\n{1}\n".format(db[seq].id, db[seq].seq))
            #results = SearchIO.index("ClusterBLAST_{0}.fasta.results".format(str(size)), "blast-tab",
            #                         fields=blast_fields)
            mainlogfile.write("Finding potential homology gaps in clusters of size {0}...\n".format(str(size)))
    
            ##### Run gap_finder. #####
            gaps = gap_finder(results, db, noncore, total, size, min_id_cutoff, strain_cutoff)
    
            ##### Identify clusters that need to be merged and move merged clusters to appropriate dictionary. #####
            for cluster in gaps:
                if cluster in noncore:
                    cluster_strains = [i.split("|")[0] for i in filter(lambda x: x != "----------", noncore[cluster])]
                    for candidate in gaps[cluster]:
                        if candidate in noncore:
                            candidate_strains = [i.split("|")[0] for i in
                                                 filter(lambda x: x != "----------", noncore[candidate])]
                            if len(set(candidate_strains) & set(cluster_strains)) == 0:
                                merge_size = (len(filter(lambda x: x != "----------", noncore[cluster])) + len(
                                    filter(lambda x: x != "----------", noncore[candidate])))
                                if merge_size == total:
                                    mainlogfile.write("{0} (size: {1}) has a homologous cluster: {2} (size: {3})\n".format
                                                      (cluster, len(filter(lambda x: x != "----------", noncore[cluster])),
                                                       candidate, len(filter(lambda x: x != "----------", noncore[candidate]))))
                                    mainlogfile.write(
                                        "Merging smaller cluster {0} into larger cluster {1}...\n".format(candidate, cluster))
                                    mainlogfile.write("Merged cluster {0} has size {1}.\n".format(cluster, merge_size))
                                    filled = merge_clusters(noncore[cluster], noncore[candidate])
                                    softcore[cluster] = filled
                                    del noncore[cluster], noncore[candidate]
                                    filled_count = filled_count + 2
                                elif merge_size < total:
                                    mainlogfile.write("{0} (size: {1}) has a homologous cluster: {2} (size: {3})\n".format
                                                      (cluster, len(filter(lambda x: x != "----------", noncore[cluster])),
                                                       candidate, len(filter(lambda x: x != "----------", noncore[candidate]))))
                                    mainlogfile.write(
                                        "Merging smaller cluster {0} into larger cluster {1}...\n".format(candidate, cluster))
                                    mainlogfile.write("Merged cluster {0} has size {1}.\n".format(cluster, merge_size))
                                    merged = merge_clusters(noncore[cluster], noncore[candidate])
                                    noncore[cluster] = merged
                                    del noncore[candidate]
                                    merged_count = merged_count + 2
    
            mainlogfile.write(
                "At cluster size (n = {0}): merged {1} homologous clusters into {2} softcore clusters.\n".format(size,
                                                                                                                 filled_count,
                                                                                                                 filled_count / 2))
            mainlogfile.write(
                "At cluster size (n = {0}): merged {1} homologous clusters into {2} noncore clusters.\n".format(size,
                                                                                                               merged_count,
                                                                                                               merged_count / 2))

            if not os.path.isdir("{0}/sub_BLASTs".format(os.getcwd())):
               os.makedirs("{0}/sub_BLASTs/faa".format(os.getcwd()))
               os.makedirs("{0}/sub_BLASTs/results".format(os.getcwd()))
            for sub_faa in glob("ClusterBLAST_*.fasta"):
               os.rename(sub_faa, "{0}/sub_BLASTs/faa/{1}".format(os.getcwd(), sub_faa))
            for sub_results in glob("*.results"):
               os.rename(sub_results, "{0}/sub_BLASTs/results/{1}".format(os.getcwd(), sub_results))

    with open("new_matchtable.txt", "w") as outmatch:
        for cluster in core:
            outmatch.write("{0}\t{1}\n".format(cluster, "\t".join(core[cluster])))
        for cluster in softcore:
            outmatch.write("{0}\t{1}\n".format(cluster, "\t".join(softcore[cluster])))
        for cluster in noncore:
            outmatch.write("{0}\t{1}\n".format(cluster, "\t".join(noncore[cluster])))

    with open("new_softtable.txt", "w") as outsofmatch:
        for cluster in softcore:
            outsofmatch.write("{0}\t{1}\n".format(cluster, "\t".join(softcore[cluster])))

    with open("new_nontable.txt", "w") as outnonmatch:
        for cluster in noncore:
            outnonmatch.write("{0}\t{1}\n".format(cluster, "\t".join(noncore[cluster])))

    with open("softcore_pam.txt", "w") as outsof:
        for cluster in softcore:
            pa = []
            for el in softcore[cluster]:
                if el == "----------":
                    pa.append("0")
                else:
                    pa.append("1")
            outsof.write("{0}\n".format("\t".join(pa)))


    with open("noncore_pam.txt", "w") as outnon:
        for cluster in noncore:
            pa = []
            for el in noncore[cluster]:
                if el == "----------":
                    pa.append("0")
                else:
                    pa.append("1")
            outnon.write("{0}\n".format("\t".join(pa)))

    sizes_arg = []
    counts_arg = []
    n_sizes = Counter([len(filter(lambda x: x != "----------", noncore[cluster])) for cluster in noncore])

    for n_size in n_sizes:
        if int(n_size) < 10:
            sizes_arg.append("n0" + str(n_size))
        else:
            sizes_arg.append("n" + str(n_size))
        counts_arg.append(str(n_sizes[n_size] * int(n_size)))

    core_count = len(flatten(core.values())) + len(filter(lambda x: x != "----------", flatten(softcore.values())))
    sizes_arg.append("n" + str(total))
    counts_arg.append(str(core_count))

    core_proteome = len(flatten(core.values()))
    softcore_proteome = len(filter(lambda x: x != "----------", flatten(softcore.values())))
    noncore_proteome = len(filter(lambda x: x != "----------", flatten(noncore.values())))

    mainlogfile.write("====Core: {0} clusters, {1} proteins."
                      "Softcore: {2} clusters, {3} proteins."
                      "Accessory: {4} clusters, {5} proteins.\n====".format(core.keys(), core_proteome, softcore.keys(), softcore_proteome, noncore.keys(), noncore_proteome))



    ring_plot = ["Rscript", "{0}/PlotRingChart.R".format(dirname), str(core_proteome), str(softcore_proteome), str(noncore_proteome), ",".join(size for size in sizes_arg), ",".join(count for count in counts_arg)]
    try:
        sp.check_call(ring_plot)
        mainlogfile.write("Creating ring chart in R...\n")
    except sp.CalledProcessError as r_exec:
        if r_exec.returncode != 0:
            mainlogfile.write("Unable to run R script PlotRingChart.R, attempted command below:\n")
            mainlogfile.write(" ".join(ring_plot) + "\n")

    upset_plot = ["Rscript", "PlotUsingUpSet.R", "softcore_pam.txt", "softcore_upset.eps"]
    try:
        sp.check_call(upset_plot)
        mainlogfile.write("Creating upset plot of softcore clusters in R...\n")
        upset_plot = ["Rscript", "PlotUsingUpSet.R", "noncore_pam.txt", "noncore_upset.eps"]
        try:
            sp.check_call(upset_plot)
        except sp.CalledProcessError as r_exec:
            if r_exec.returncode != 0:
                mainlogfile.write("Unable to run R script PlotUsingUpSet.R. Run command manually:\n")
                mainlogfile.write(" ".join(upset_plot) + "\n")
    except sp.CalledProcessError as r_exec:
        if r_exec.returncode != 0:
            mainlogfile.write("Unable to run R script PlotUsingUpSet.R. Run command manually:\n")
            mainlogfile.write(" ".join(upset_plot) + "\n")

    mainlogfile.write("Remaining noncore clusters after prediction analysis: {0}\n".format(len(noncore)))
    mainlogfile.write(
        "prediction analysis finished in {0} seconds. Thank you for choosing prediction, the friendly pangenome software.\n".format(
            time.time() - start_time))
    mainlogfile.write("=== Finished prediction job at {0}. ===\n".format(str(datetime.datetime.now())))


##### Here we define the workflow for prediction. #####


def main():
    """
    Main software workflow.
    """
    cluster_clean("matchtable.txt", "panoct_db.fasta", split_by=cores, strain_cutoff=1.0)


if __name__ == "__main__":
    ##### Open log files. #####
    mainlogfile = open("prediction.log", "a", 0)  # Flush to log immediately.
    mainlogfile.write("\n=== Started prediction job at {0}. ===\n".format(str(datetime.datetime.now())))
    start_time = time.time()
    subblastlog = open("parallel_BLAST.log", "a", 0)  # Log for parallel_BLAST.
    subblastlog.write("\n=== Started prediction job at {0}. ===\n".format(str(datetime.datetime.now())))

    ##### Define custom columns for parallel_BLAST. #####
    blast_fields = ['qseqid', 'sseqid', 'pident', 'length', 'mismatch', 'gapopen',
                    'qstart', 'qend', 'sstart', 'send', 'evalue', 'bitscore',
                    'qlen', 'slen']

    ##### Set default amount of cores used. #####
    cores = mp.cpu_count() - 1

    ##### Get absolute path of script, for running R commands. #####
    dirname = os.path.dirname(os.path.abspath(__file__))

    ##### Run prediction. #####
    main()
