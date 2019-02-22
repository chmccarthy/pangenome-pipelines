# Pan-genome analyses of model Fungal species.

**Charley G. P. McCarthy & David A. Fitzpatrick (2019). *Microbial Genomics* 5(1). doi: 10.1099/mgen.0.000243. [Link](https://mgen.microbiologyresearch.org/content/journal/mgen/10.1099/mgen.0.000243).**

**Please note: I am currently doing a wholesale rewrite of/substantial improvement on the pipelines in this repository for general fungal/microbial pangenome analysis, I will link to this repository from here when a near-complete version is available.**

This repository holds custom pipelines used to analyse the pan-genomes of four model fungal species; *Saccharomyces cerevisiae*, *Candida albicans*, *Cryptococcus neoformans* var. *grubii* and *Aspergillus fumigatus*.

The pipelines are as follows:
+ A custom gene model prediction pipeline which uses parallelized `Exonerate` searches, `GeneMark-ES` HMM prediction and `TransDecoder` coding potential prediction to generate gene model sets for each strain genome in a pan-genome dataset.

+ A post-processing pipeline which takes the results of PanOCT output and attempts to merge non-core syntenic clusters of gene models which have reciprocal sequence similarity between each other but have otherwise lost microsynteny using iterative BLAST+ searches and parsing. This pipeline also runs two R scripts to generate ring charts of the size of species core and accessory genomes as well as an UpSet distribution of accessory gene model clusters within pan-genomes.

## Requirements

Each analysis in McCarthy & Fitzpatrick (2019) using these pipelines was performed on a HPC cluster with a PBS scheduler. These pipelines have also been tested in other Linux and macOS environments.

- Python
  - [Biopython](https://biopython.org/)

- R
  - [Cairo](https://cran.r-project.org/web/packages/Cairo/index.html)
  - [UpSetR](https://cran.r-project.org/web/packages/UpSetR/README.html)
  - [ggplot2](https://ggplot2.tidyverse.org/)
  - [ggrepel](https://cran.r-project.org/web/packages/ggrepel/index.html)

- [BLAST+](https://blast.ncbi.nlm.nih.gov/Blast.cgi?PAGE_TYPE=BlastDocs&DOC_TYPE=Download)

- [Exonerate](https://www.ebi.ac.uk/about/vertebrate-genomics/software/exonerate)

- [GeneMark-ES](http://exon.gatech.edu/GeneMark/gmes_instructions.html)

- [TransDecoder](https://github.com/TransDecoder/TransDecoder/wiki)
