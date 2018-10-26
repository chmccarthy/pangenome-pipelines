library(UpSetR)

clusters<-read.table("presence_absence.txt", header = F)
sets <- readLines("list_of_strains.txt")
colnames(clusters) <- sets
orders <- sets
upset(clusters, order.by = "freq", sets = rev(orders), keep.order = TRUE,
      mainbar.y.label = "Number of clusters", sets.x.label = "Number of gene models",
      mb.ratio = c(1, 0), nintersects=100)


