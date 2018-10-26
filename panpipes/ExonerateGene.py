from Bio import SearchIO

"""
ExonerateGene: Gene object called through exonerate.
"""


class ExonerateGene:
    """
    An object that stores the attributes of a gene called via exonerate.
    """

    def __init__(self, string):
        """
        Define the attributes of a ExonerateGene object.

        - contig_id:     ID of source contig.
        - locs:          Genomic location of called gene on contig.
        - gene_id:       ID of called gene, derived from contig_id and locs.
        - ref:           Reference homolog (i.e. seed gene for exonerate).
        - internal_stop: Internal stop codon present or not.
        - introns:       Number of introns in called gene.
        - called:        Called gene's translated protein sequence.

        All attributes above are derived ultimately from exonerate output.

        Note: locs are always given in "positive" sense, regardless of gene's
        actual sense, this is consistent with Biopython.SearchIO.

        Note: In weird cases, exonerate-text returns a negative start
        co-ordinate for some (not all?) reverse complement genes. I put in
        a second parse using exonerate-vulgar to determine co-ordinates for
        genes.
        """
        contig_id = ""
        introns = 0
        called = []
        for result in SearchIO.parse(string, "exonerate-text"):
            ref = result.id
            stop = False
            for hit in result:
                contig_id = hit.id
                called = []
                introns = len(hit[0].hit_inter_ranges)
                for fragment in hit[0].fragments:
                    for record in fragment.aln._records:
                        if record.name == "aligned hit sequence":
                            called.append(str(record.seq))
                            if "*" in record.seq[:-1]:
                                stop = True
            self.ref = "Exonerate={0}".format(str(ref))
            self.contig_id = contig_id
            self.internal_stop = "IS={0}".format(str(stop))
            self.introns = "Introns={0}".format(str(introns))
            self.called = "".join(called)
        string.seek(0)
        for result in SearchIO.parse(string, "exonerate-vulgar"):
            for hit in result:
                locs = hit[0].hit_range
                gene_id = "{0}_{1}".format(hit.id, "_".join(str(loc) for
                                                            loc in locs))
                self.locs = locs
                self.id = gene_id

    def __str__(self):
        """
        Return a string summary of a called gene, a la Biopython.SeqIO.

        Useful for debugging.
        """
        lines = []
        if not self.contig_id:
            lines.append("Contig ID: No hit on genome.")
        else:
            lines.append("Contig ID: {0}".format(self.contig_id))
        if not self.locs:
            lines.append("Gene location: No hit on genome.")
        else:
            lines.append("Gene location: {0}".format(str(self.locs)))
        if not self.id:
            lines.append("Gene ID: No hit on genome.")
        else:
            lines.append("Gene ID: {0}".format(self.id))
        if not self.ref:
            lines.append("Reference homolog: No hit on genome.")
        else:
            lines.append("Reference homolog: {0}".format(self.ref))
        if not self.internal_stop:
            lines.append("Internal stop codons: None")
        else:
            lines.append("Internal stop codons: True")
        if not self.introns:
            lines.append("Number of introns: 0")
        else:
            lines.append("Number of introns: {0}".format(str(self.introns)))
        if not self.called:
            lines.append("Called protein sequence: No hit on genome.")
        else:
            lines.append("Called protein sequence: {0}\n".format(self.called))
        return "\n".join(lines)
