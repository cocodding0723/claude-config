#!/usr/bin/perl
# 표준 입력을 줄 단위로 치환 규칙(rename-rules.tsv)에 따라 변환해 표준 출력으로 냄. 규칙 파일 경로가 첫 인자.
use strict;
use warnings;
use utf8;
binmode(STDIN, ':encoding(UTF-8)');
binmode(STDOUT, ':encoding(UTF-8)');

my @rules;
open(my $fh, '<:encoding(UTF-8)', $ARGV[0]) or die "규칙 파일을 열 수 없음: $ARGV[0]\n";
while (my $l = <$fh>) {
    chomp $l;
    next if $l =~ /^\s*(#|$)/;
    my ($pat, $rep) = split /\t/, $l, 2;
    die "규칙 형식 오류: $l\n" unless defined $rep;
    push @rules, [qr/$pat/, $rep];
}
close $fh;

while (my $line = <STDIN>) {
    for my $r (@rules) {
        my ($re, $rep) = @$r;
        $line =~ s/$re/$rep/g;
    }
    print $line;
}
