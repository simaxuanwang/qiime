#!/usr/bin/env python
# File created on 22 Mar 2010
from __future__ import division

__author__ = "Greg Caporaso"
__copyright__ = "Copyright 2011, The QIIME Project"
__credits__ = ["Greg Caporaso"]
__license__ = "GPL"
__version__ = "1.2.1-dev"
__maintainer__ = "Greg Caporaso"
__email__ = "gregcaporaso@gmail.com"
__status__ = "Development"

from itertools import izip
from os.path import split, splitext
from os import makedirs
from numpy import log10, median, arange, histogram
from cogent import DNA
from cogent.parse.fastq import MinimalFastqParser
from qiime.format import format_histogram_one_count

class FastqParseError(Exception):
    pass

def get_illumina_qual_chars():
    return '@ABCDEFGHIJKLMNOPQRSTUVWXYZ[\]^_`abcdefghijklmnopqrstuvwxyz{|}~'

def bad_chars_from_threshold(first_bad_char):
    if first_bad_char == '':
        return {}
    else:
        all_chars = get_illumina_qual_chars()
        first_bad_char_index = all_chars.index(first_bad_char)
        bad_chars = list(get_illumina_qual_chars()[:first_bad_char_index+1])
        return {}.fromkeys(bad_chars)

def read_qual_score_filter(seq,qual,max_run_length,threshold):
    """slices illumina sequence and quality line based on quality filter
    """
    bad_chars = bad_chars_from_threshold(threshold)
    last_good_slice_end_pos = 0
    bad_run_length = 0
    for i in range(len(seq)):
        if qual[i] in bad_chars:
            bad_run_length += 1
        else:
            bad_run_length = 0
            last_good_slice_end_pos = i + 1
            
        if bad_run_length > max_run_length:
            return seq[:last_good_slice_end_pos],\
                   qual[:last_good_slice_end_pos]
    
    # There were no runs that were too bad for too long 
    return seq, qual

def quality_filter_sequence(header,
                            sequence,
                            quality,
                            max_bad_run_length,
                            first_bad_quality_char,
                            min_per_read_length,
                            seq_max_N,
                            filter_bad_illumina_qual_digit):
    if filter_bad_illumina_qual_digit:
        h = header.split()[0]
        try:
            # this block is a little strange because each of these 
            # can throw a ValueError. The same thing needs to be done
            # in either case, so it doesn't really make sense to split
            # into two separate try/excepts, particulary because that would
            # complicate the logic
            quality_char = header[h.index('#')+1]
            illumina_quality_digit = int(quality_char)
        except ValueError:
            pass
        else:
            if illumina_quality_digit == 0:
                return 3, sequence, quality
        
    sequence, quality = read_qual_score_filter(sequence,
                                       quality,
                                       max_bad_run_length, 
                                       first_bad_quality_char)
                                       
    if (len(sequence) < min_per_read_length):
        return 1, sequence, quality
    elif (sequence.count('N') > seq_max_N):
        return 2, sequence, quality
    else:
        return 0, sequence, quality

def check_header_match(header1,header2):
    
    # split on '#' and '/' to handle cases with and without the
    # Illumina quality digit
    header1 = header1.split('#')[0].split('/')[0]
    header2 = header2.split('#')[0].split('/')[0]
    
    return header1 == header2


def process_fastq_single_end_read_file(fastq_read_f,
                                       fastq_barcode_f,
                                       barcode_to_sample_id,
                                       store_unassigned=False,
                                       max_bad_run_length=0,
                                       first_bad_quality_char='B',
                                       min_per_read_length=75,
                                       rev_comp=False,
                                       rev_comp_barcode=False,
                                       seq_max_N=0,
                                       start_seq_id=0,
                                       filter_bad_illumina_qual_digit=True,
                                       log_f=None,
                                       histogram_f=None):
    """parses fastq single-end read file
    """
    header_index = 0
    sequence_index = 1
    quality_index = 2
    
    seq_id = start_seq_id
    
    # prep data for logging
    input_sequence_count = 0
    count_barcode_not_in_map = 0
    count_too_short = 0
    count_too_many_N = 0
    count_bad_illumina_qual_digit = 0
    sequence_lengths = []
    seqs_per_sample_counts = {}
    
    for bc_data,read_data in izip(MinimalFastqParser(fastq_barcode_f,strict=False),
                                  MinimalFastqParser(fastq_read_f,strict=False)):
        input_sequence_count += 1
        # Confirm match between barcode and read headers
        if not check_header_match(bc_data[header_index],
                                  read_data[header_index]):
            raise FastqParseError,\
             ("Headers of barcode and read do not match. Can't continue. "
              "Confirm that the barcode fastq and read fastq that you are "
              "passing match one another.")
        else:
            header = read_data[header_index]
        
        # Grab the barcode sequence
        barcode = bc_data[sequence_index]
        if rev_comp_barcode:
            barcode = DNA.rc(barcode)
        # Grab the read sequence
        sequence = read_data[1]
        # Grab the read quality
        quality = read_data[2]
        
        try:
          sample_id = barcode_to_sample_id[barcode]
        except KeyError:
          if not store_unassigned:
              count_barcode_not_in_map += 1
              continue
          else:
              sample_id = 'Unassigned'
        
        quality_filter_result, sequence, quality =\
          quality_filter_sequence(header,
                                  sequence,
                                  quality,
                                  max_bad_run_length,
                                  first_bad_quality_char,
                                  min_per_read_length,
                                  seq_max_N,
                                  filter_bad_illumina_qual_digit)
        
        # process quality result
        if quality_filter_result != 0:
            # if the quality filter didn't pass record why and 
            # move on to the next record
            if quality_filter_result == 1:
                count_too_short += 1
            elif quality_filter_result == 2:
                count_too_many_N += 1
            elif quality_filter_result == 3:
                count_bad_illumina_qual_digit += 1
            else:
                raise ValueError,\
                 "Unknown quality filter result: %d" % quality_filter_result
            continue
        
        sequence_lengths.append(len(sequence))
        
        try:
            seqs_per_sample_counts[sample_id] += 1
        except KeyError:
            seqs_per_sample_counts[sample_id] = 1
        
        if rev_comp:
            sequence = DNA.rc(sequence)
            quality = quality[::-1]
        
        fasta_header = '%s_%s %s' % (sample_id,seq_id,header)
        yield fasta_header, sequence, quality, seq_id
        seq_id += 1

    if log_f != None:
        log_str = format_log(count_barcode_not_in_map,
                             count_too_short,
                             count_too_many_N,
                             count_bad_illumina_qual_digit,
                             input_sequence_count,
                             sequence_lengths,
                             seqs_per_sample_counts)
        log_f.write(log_str)
    
    if histogram_f != None:
        counts, bin_edges = make_histograms(sequence_lengths)
        histogram_str = format_histogram_one_count(counts,bin_edges)
        histogram_f.write(histogram_str)

def format_log(count_barcode_not_in_map,
               count_too_short,
               count_too_many_N,
               count_bad_illumina_qual_digit,
               input_sequence_count,
               sequence_lengths,
               seqs_per_sample_counts):
    """ Format the split libraries log """
    log_out = ["Quality filter results"]
    log_out.append("Total number of input sequences: %d" % input_sequence_count)
    log_out.append("Barcode not in mapping file: %d" % count_barcode_not_in_map)
    log_out.append("Read too short after quality truncation: %d" % count_too_short)
    log_out.append("Count of N characters exceeds limit: %d" % count_too_many_N)
    log_out.append("Illumina quality digit = 0: %d" % count_bad_illumina_qual_digit)
    
    log_out.append("")
    
    log_out.append("Result summary (after quality filtering)")
    log_out.append("Median sequence length: %1.2f" % median(sequence_lengths))
    counts = [(v,k) for k,v in seqs_per_sample_counts.items()]
    counts.sort()
    for sequence_count, sample_id in counts:
        log_out.append('%s\t%d' % (sample_id,sequence_count))
    return '\n'.join(log_out)
    
def make_histograms(lengths, binwidth=10):
    """Makes histogram data for pre and post lengths"""
    min_len = min(lengths)
    max_len = max(lengths)
    floor = (min_len/binwidth)*binwidth
    ceil = ((max_len/binwidth)+2)*binwidth
    bins = arange(floor, ceil, binwidth)
    hist, bin_edges = histogram(lengths,bins)
    return hist, bin_edges