import sys
import pathogenprofiler as pp
import json
from collections import defaultdict
from .utils import get_genome_positions_from_json_db, get_lt2drugs,rv2genes
from .xdb import *

def get_summary(json_results,conf,columns = None,drug_order = None,reporting_af=0.0):
    if not columns:
        columns=[]
    drugs = set()
    for l in open(conf["bed"]):
        arr = l.rstrip().split()
        for d in arr[5].split(","):
            drugs.add(d)
    if drug_order:
        drugs = drug_order
    drug_table = []
    results = {}
    annotation = {}
    for key in columns:
        if key not in json_results["dr_variants"][0]: pp.errlog("%s not found in variant annotation, is this a valid column in the database CSV file? Exiting!" % key,True)
    for x in json_results["dr_variants"]:
        for d in x["drugs"]:
            drug = d["drug"]
            if float(x["freq"])<reporting_af:continue
            if drug not in results: results[drug] = []
            results[d["drug"]].append("%s %s (%.2f)" % (x["gene"],x["change"],float(x["freq"])))
            if drug not in annotation: annotation[drug] = {key:[] for key in columns}
            for key in columns:
                annotation[drug][key].append(x["drugs"][drug][key])
    if "resistance_genes" in json_results:
        for x in json_results["resistance_genes"]:
            for d in x["drugs"]:
                drug = d["drug"]
                if drug not in results: results[drug] = []
                results[d["drug"]].append("%s (resistance_gene)" % (x["gene"],))
                if drug not in annotation: annotation[drug] = {key:[] for key in columns}
                for key in columns:
                    annotation[drug][key].append(x["drugs"][drug][key])


    for d in drugs:
        if d in results:
            results[d] = ", ".join(results[d]) if len(results[d])>0 else ""
            r = "R" if len(results[d])>0 else ""
            for key in columns:
                annotation[d][key] = ", ".join(annotation[d][key]) if len(annotation[d][key])>0 else ""
        else:
            results[d] = ""
            r = ""
        dictline = {"Drug":d.capitalize(),"Genotypic Resistance":r,"Mutations":results[d]}
        for key in columns:
            dictline[key] = annotation[d][key] if d in annotation else ""
        drug_table.append(dictline)
    new_json = json_results.copy()
    new_json["drug_table"] = drug_table
    return new_json

def select_most_relevant_csq(csqs):
    rank = ["transcript_ablation","frameshift_variant","large_deletion","start_lost","disruptive_inframe_deletion","disruptive_inframe_insertion","stop_gained","stop_lost","conservative_inframe_deletion","conservative_inframe_insertion","initiator_codon_variant","missense_variant","non_coding_transcript_exon_variant","upstream_gene_variant","stop_retained_variant","synonymous_variant"]
    ranked_csq = []
    for csq in csqs:
        ranked_csq.append([i for i,d in enumerate(rank) if d in csq["type"]][0])
    csq1 = csqs[ranked_csq.index(min(ranked_csq))]
    return csq1

def set_change(var):
    protein_csqs = ["missense_variant","stop_gained"]
    var["change"] = var["protein_change"] if var["type"] in protein_csqs else var["nucleotide_change"]
    return var

def select_csq(dict_list):
    for d in dict_list:
        annotated_csq = []
        for csq in d["consequences"]:
            if "annotation" in csq:
                annotated_csq.append(csq)
        if len(annotated_csq)==0:
            csq = select_most_relevant_csq(d["consequences"])
            alternate_consequences = [json.dumps(x) for x in d["consequences"]]
            alternate_consequences.remove(json.dumps(csq))
            alternate_consequences = [json.loads(x) for x in alternate_consequences]
        elif len(annotated_csq)==1:
            csq = annotated_csq[0]
            alternate_consequences = []
        else:
            quit("ERROR! too many csqs")
        del d["consequences"]
        d.update(csq)
        d["alternate_consequences"] = alternate_consequences
        d = set_change(d)
    return dict_list

def dict_list_add_genes(dict_list,conf):
    rv2gene = {}
    for l in open(conf["bed"]):
        row = l.rstrip().split()
        rv2gene[row[3]] = row[4]
    for d in dict_list:
        d["locus_tag"] = d["gene_id"]
        d["gene"] = rv2gene[d["gene_id"]]
        del d["gene_id"]
        if "gene_name" in d:
            del d["gene_name"]
    return dict_list

def get_main_lineage(lineage_dict_list,max_node_skip=1):
    def collapse_paths(paths):
        filtered_paths = []
        for p in sorted(paths,reverse=True):
            if p=="lineageBOV_AFRI": continue
            path_stored = any([p in x for x in filtered_paths])
            if not path_stored:
                filtered_paths.append(p)
        return filtered_paths

    def derive_path(x):
        return [".".join(x.split(".")[:i])for i in range(1,len(x.split(".")))] + [x]

    lin_freqs = {}
    pool = []
    for l in lineage_dict_list:
        pool.append(l["lin"].replace("M.","M_"))
        lin_freqs[l["lin"].replace("M.","M_")] = float(l["frac"])
    routes = [";".join(derive_path(x)) for x in pool]
    paths = collapse_paths(routes)
    path_mean_freq = {}
    for path in paths:
        nodes = tuple(path.split(";"))
        nodes_skipped = sum([n not in pool for n in nodes])
        if nodes_skipped>max_node_skip: continue
        freqs = [lin_freqs[n] for n in nodes if n in lin_freqs]
        path_mean_freq[nodes] = sum(freqs)/len(freqs)
    main_lin = ";".join(sorted(list(set([x[0] for x in path_mean_freq])))).replace("_",".")
    sublin = ";".join([x[-1] for x in path_mean_freq]).replace("_",".")
    return (main_lin,sublin)

def barcode2lineage(results,max_node_skip=1):
    results["lineage"] = []
    for d in results["barcode"]:
        results["lineage"].append({"lin":d["annotation"],"family":d["info"][0],"spoligotype":d["info"][1],"rd":d["info"][2],"frac":d["freq"]})
    del results["barcode"]
    results["lineage"] = sorted(results["lineage"],key= lambda x:len(x["lin"]))
    main_lin,sublin = get_main_lineage(results["lineage"])
    results["main_lin"] = main_lin
    results["sublin"] = sublin
    return results


def reformat_annotations(results,conf):
    #Chromosome      4998    Rv0005  -242
    lt2drugs = get_lt2drugs(conf["bed"])
    results["dr_variants"] = []
    results["other_variants"] = []
    for var in results["variants"]:
        if "annotation" in var:
            tmp = var.copy()
            drugs = tuple([x["drug"] for x in var["annotation"] if x["type"]=="drug" and x["confers"]=="resistance"])
            if len(drugs)>0:
                dr_ann = []
                other_ann = []
                while len(tmp["annotation"])>0:
                    x = tmp["annotation"].pop()
                    if x["type"]=="drug":
                        dr_ann.append(x)
                    else:
                        other_ann.append(x)
                tmp["drugs"] = dr_ann
                tmp["annotation"] = other_ann
                results["dr_variants"].append(tmp)
            else:
                var["gene_associated_drugs"] = lt2drugs[var["locus_tag"]]
                results["other_variants"].append(var)

        else:
            var["gene_associated_drugs"] = lt2drugs[var["locus_tag"]]
            results["other_variants"].append(var)
    del results["variants"]
    return results

def add_drtypes(results,reporting_af=0.1):
    resistant_drugs = set()
    for var in results["dr_variants"]:
        if var["freq"]>=reporting_af:
            for d in var["drugs"]:
                resistant_drugs.add(d["drug"])

    FLQ_set = set(["levofloxacin","moxifloxacin","ciprofloxacin","ofloxacin"])
    groupA_set = set(["bedaquiline","linezolid"])

    rif = "rifampicin" in resistant_drugs
    inh = "isoniazid" in resistant_drugs
    flq = len(FLQ_set.intersection(resistant_drugs)) > 0
    gpa = len(groupA_set.intersection(resistant_drugs)) > 0

    if len(resistant_drugs)==0:
        drtype = "Sensitive"
    elif (rif and not inh) and not flq:
        drtype = "RR-TB"
    elif (inh and not rif):
        drtype = "HR-TB"
    elif (rif and inh) and not flq:
        drtype = "MDR-TB"
    elif rif and (flq and not gpa):
        drtype = "Pre-XDR-TB"
    elif rif and (flq and gpa):
        drtype = "XDR-TB"
    else:
        drtype = "Other"


    results["drtype"] = drtype
    return results

unlist = lambda t: [item for sublist in t for item in sublist]

def reformat_missing_genome_pos(positions,conf):
    rv2gene = rv2genes(conf["bed"])
    dr_associated_genome_pos = get_genome_positions_from_json_db(conf["json_db"])
    new_results = []
    for pos in positions:
        if pos in dr_associated_genome_pos:
            tmp = dr_associated_genome_pos[pos]
            gene = list(tmp)[0][0]
            variants = ",".join([x[1] for x in tmp])
            drugs = ",".join(set(unlist([x[2] for x in tmp])))
            new_results.append({"position":pos,"locus_tag":gene, "gene": rv2gene[gene], "variants": variants, "drugs":drugs})
    return new_results


def suspect_profiling(results):
    new_vars = []
    for var in results["other_variants"]:
        if var["type"]!="missense_variant": continue
        pred = None
        if var["gene"]=="atpE":
            pred = get_biosig_bdq_prediction(var["change"])
        if var["gene"]=="pncA":
            pred = get_biosig_pza_prediction(var["change"])
        if pred:
            if "annotation" in var:
                var["annotation"].append(pred)
            else:
                var["annotation"] = [pred]
            var["drugs"] = [{
                "type":"drug",
                "drug":"pyrazinamide" if var["gene"]=="pncA" else "bedquiline",
                "confers": "resistance",
                "evidence": "suspect-PZA" if var["gene"]=="pncA" else "suspect-BDQ"
            }]
            new_vars.append(var)
    for v in new_vars:
        results["dr_variants"].append(v)
        results["other_variants"].remove(v)
    return results

    
def reformat(results,conf,reporting_af,mutation_metadata=False,use_suspect=False):
    results["variants"] = [x for x in results["variants"] if len(x["consequences"])>0]
    results["variants"] = select_csq(results["variants"])
    results["variants"] = dict_list_add_genes(results["variants"],conf)
    if "gene_coverage" in results["qc"]:
        results["qc"]["gene_coverage"] = dict_list_add_genes(results["qc"]["gene_coverage"],conf)
        results["qc"]["missing_positions"] = reformat_missing_genome_pos(results["qc"]["missing_positions"],conf)
    if "barcode" in results:
        # results["barcode"] = []
        results = barcode2lineage(results)
    results = reformat_annotations(results,conf)
    results = add_drtypes(results,reporting_af)
    results["db_version"] = json.load(open(conf["version"]))
    if mutation_metadata:
        pass
        # results = add_mutation_metadata(results)
    if use_suspect:
        results = suspect_profiling(results)
    return results
