from collections import OrderedDict
import gzip
import io
import pickle

import msgpack
import msgpack_numpy
import pandas
import snappy


class NoneCompressor(object):
    name = 'none'

    def compress(self, data):
        return data

    def decompress(self, data):
        return data


class GzipCompressor(object):
    name = 'gzip'

    def compress(self, data):
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode='wb', compresslevel=1) as f:
            f.write(data)
        return buf.getvalue()

    def decompress(self, data):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as f:
            return f.read()


class SnappyCompressor(object):
    name = 'snappy'

    def compress(self, data):
        return snappy.compress(data)

    def decompress(self, data):
        return snappy.decompress(data)


class MsgPackSerializer(object):
    name = 'msgpack'

    def encode(self, obj, container):
        if container == 'dataframe':
            return obj.to_msgpack()
        elif container == 'ndarray':
            return msgpack.packb(obj, default=msgpack_numpy.encode)
        elif container == 'python':
            return msgpack.packb(obj, use_bin_type=True)
        else:
            raise ValueError('unknown container: %s' % container)

    def decode(self, bytestr, container):
        if container == 'dataframe':
            return pandas.read_msgpack(bytestr)
        elif container == 'ndarray':
            return msgpack.unpackb(bytestr, object_hook=msgpack_numpy.decode)
        elif container == 'python':
            return msgpack.unpackb(bytestr, encoding='utf-8')
        else:
            raise ValueError('unknown container: %s' % container)


class PickleSerializer(object):
    def __init__(self, protocol_level):
        self._protocol_level = protocol_level
        self.name = 'pickle%d' % protocol_level

    def encode(self, obj, container):
        return pickle.dumps(obj, protocol=self._protocol_level)

    def decode(self, bytestr, container):
        return pickle.loads(bytestr)


class ComboSerializer(object):
    def __init__(self, format_encoder, compressor):
        self._format_encoder = format_encoder
        self._compressor = compressor
        self.format_name = format_encoder.name
        self.compressor_name = compressor.name

    def encode(self, obj, container):
        return self._compressor.compress(self._format_encoder.encode(obj, container))

    def decode(self, bytestr, container):
        return self._format_encoder.decode(self._compressor.decompress(bytestr), container)


# Insert in preference order
picklers = [PickleSerializer(protocol) for protocol in range(pickle.HIGHEST_PROTOCOL + 1)]
serializers = [MsgPackSerializer()] + picklers
format_registry = OrderedDict([(e.name, e) for e in serializers])

compressors = [SnappyCompressor(), GzipCompressor(), NoneCompressor()]
compression_registry = OrderedDict([(e.name, e) for e in compressors])
