#!/usr/bin/env python
#
# Copyright (c) 2011, Andres Moreira <andres@andresmoreira.com>
#               2011, Felipe Cruz <felipecruz@loogica.net>
#               2012, JT Olds <jt@spacemonkey.com>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#     * Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#     * Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#     * Neither the name of the authors nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL ANDRES MOREIRA BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#

"""python-snappy

Python library for the snappy compression library from Google.
Expected usage like:

    import snappy

    compressed = snappy.compress("some data")
    assert "some data" == snappy.uncompress(compressed)

"""

import struct

from _snappy import CompressError, CompressedLengthError, \
                    InvalidCompressedInputError, UncompressError, \
                    compress, decompress, isValidCompressed, uncompress, \
                    _crc32c

_CHUNK_MAX = 32768
_STREAM_IDENTIFIER = b"sNaPpY"
_COMPRESSED_CHUNK = 0x00
_UNCOMPRESSED_CHUNK = 0x01
_IDENTIFIER_CHUNK = 0xff
_RESERVED_UNSKIPPABLE = (0x02, 0x80)  # chunk ranges are [inclusive, exclusive)
_RESERVED_SKIPPABLE = (0x80, 0x100)

def _masked_crc32c(data):
    # see the framing format specification
    crc = _crc32c(data)
    return (((crc >> 15) | (crc << 17)) + 0xa282ead8) & 0xffffffff

_compress = compress
_uncompress = uncompress


class StreamCompressor(object):

    """This class implements the compressor-side of the proposed Snappy framing
    format, found at

        http://code.google.com/p/snappy/source/browse/trunk/framing_format.txt
            ?spec=svn68&r=55

    This class matches the interface found for the zlib module's compression
    objects (see zlib.compressobj), but also provides some additions, such as
    the snappy framing format's ability to intersperse uncompressed data.

    Keep in mind that this compressor object does no buffering for you to
    appropriately size chunks. Every call to StreamCompressor.compress results
    in a unique call to the underlying snappy compression method.
    """

    __slots__ = ["_header_chunk_written"]

    def __init__(self):
        self._header_chunk_written = False

    def add_chunk(self, data, compress=True):
        """Add a chunk containing 'data', returning a string that is framed and
        (optionally, default) compressed. This data should be concatenated to
        the tail end of an existing Snappy stream. In the absence of any
        internal buffering, no data is left in any internal buffers, and so
        unlike zlib.compress, this method returns everything.
        """
        if not self._header_chunk_written:
            self._header_chunk_written = True
            out = [struct.pack("<BH", _IDENTIFIER_CHUNK,
                               len(_STREAM_IDENTIFIER)),
                   _STREAM_IDENTIFIER]
        else:
            out = []
        if compress:
            chunk_type = _COMPRESSED_CHUNK
        else:
            chunk_type = _UNCOMPRESSED_CHUNK
        for i in range(0, len(data), _CHUNK_MAX):
            chunk = data[i:i + _CHUNK_MAX]
            crc = _masked_crc32c(chunk)
            if compress:
                chunk = _compress(chunk)
            out.append(struct.pack("<BHL", chunk_type, len(chunk) + 4, crc))
            out.append(chunk)
        return b"".join(out)

    def compress(self, data):
        """This method is simply an alias for compatibility with zlib
        compressobj's compress method.
        """
        return self.add_chunk(data, compress=True)

    def flush(self, mode=None):
        """This method does nothing and only exists for compatibility with
        the zlib compressobj
        """
        pass

    def copy(self):
        """This method exists for compatibility with the zlib compressobj.
        """
        copy = StreamCompressor()
        copy._header_chunk_written = self._header_chunk_written
        return copy


class StreamDecompressor(object):

    """This class implements the decompressor-side of the proposed Snappy
    framing format, found at

        http://code.google.com/p/snappy/source/browse/trunk/framing_format.txt
            ?spec=svn68&r=55

    This class matches a subset of the interface found for the zlib module's
    decompression objects (see zlib.decompressobj). Specifically, it currently
    implements the decompress method without the max_length option, the flush
    method without the length option, and the copy method.
    """

    __slots__ = ["_buf", "_header_found"]

    def __init__(self):
        self._buf = b""
        self._header_found = False

    def decompress(self, data):
        """Decompress 'data', returning a string containing the uncompressed
        data corresponding to at least part of the data in string. This data
        should be concatenated to the output produced by any preceding calls to
        the decompress() method. Some of the input data may be preserved in
        internal buffers for later processing.
        """
        self._buf += data
        uncompressed = []
        while True:
            if len(self._buf) < 3:
                return b"".join(uncompressed)
            chunk_type, size = struct.unpack("<BH", self._buf[:3])
            if not self._header_found:
                if (chunk_type != _IDENTIFIER_CHUNK and
                        size != len(_STREAM_IDENTIFIER)):
                    raise UncompressError("stream missing snappy identifier")
                self._header_found = True
            if (_RESERVED_UNSKIPPABLE[0] <= chunk_type and
                    chunk_type < _RESERVED_UNSKIPPABLE[1]):
                raise UncompressError(
                    "stream received unskippable but unknown chunk")
            if len(self._buf) < 3 + size:
                return b"".join(uncompressed)
            chunk, self._buf = self._buf[3:3 + size], self._buf[3 + size:]
            if (_RESERVED_SKIPPABLE[0] <= chunk_type and
                    chunk_type < _RESERVED_SKIPPABLE[1]):
                continue
            assert chunk_type in (_COMPRESSED_CHUNK, _UNCOMPRESSED_CHUNK)
            crc, chunk = chunk[:4], chunk[4:]
            if chunk_type == _COMPRESSED_CHUNK:
                chunk = _uncompress(chunk)
            if struct.pack("<L", _masked_crc32c(chunk)) != crc:
                raise UncompressError("crc mismatch")
            uncompressed.append(chunk)

    def flush(self):
        """All pending input is processed, and a string containing the
        remaining uncompressed output is returned. After calling flush(), the
        decompress() method cannot be called again; the only realistic action
        is to delete the object.
        """
        if self._buf != b"":
            raise UncompressError("chunk truncated")
        return b""

    def copy(self):
        """Returns a copy of the decompression object. This can be used to save
        the state of the decompressor midway through the data stream in order
        to speed up random seeks into the stream at a future point.
        """
        copy = StreamDecompressor()
        copy._buf, copy._header_found = self._buf, self._header_found
        return copy