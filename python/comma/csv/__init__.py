import numpy
import sys
from StringIO import StringIO
import itertools
import warnings
import operator
import re
import comma.csv.format
import comma.csv.time

def shape_unrolled_types_of_flat_dtype( dtype ):
  shape_unrolled_types = []
  for descr in dtype.descr:
    type = descr[1]
    shape = descr[2] if len( descr ) > 2 else ()
    shape_unrolled_types.extend( [ type ] * reduce( operator.mul, shape, 1 ) )
  return tuple( shape_unrolled_types )

class struct:
  def __init__( self, concise_fields, *concise_types ):
    if len( concise_fields.split(',') ) != len( concise_types ):
      raise Exception( "expected {} types for '{}', got {} type(s)".format( len( concise_fields.split(',') ), concise_fields, len( concise_types )) )
    self.dtype = numpy.dtype( zip( concise_fields.split(','), concise_types ) )
    fields, types = [], []
    self.shorthand = {}
    for name, type in zip( concise_fields.split(','), concise_types ):
      if isinstance( type, struct ):
        fields_of_type = [ name + '/' + field for field in type.fields ]
        fields.extend( fields_of_type )
        types.extend( type.types )
        self.shorthand[name] = fields_of_type
        for subname,subfields in type.shorthand.iteritems():
          self.shorthand[ name + '/' + subname ] = [ name + '/' + field for field in subfields ]
      else:
        fields.append( name )
        types.append( type )
    self.fields = tuple( fields )
    self.types = tuple( types )
    self.flat_dtype = numpy.dtype( zip( self.fields, self.types ) )
    format = ','.join( shape_unrolled_types_of_flat_dtype( self.flat_dtype ) )
    number_of_types = len( re.sub( r'\([^\)]*\)', '', format ).split(',') )
    self.unrolled_flat_dtype = numpy.dtype( [ ( 'f0', format ) ] ) if number_of_types == 1 else numpy.dtype( format )

class stream:
  buffer_size_in_bytes = 65536
  def __init__( self, s, fields='', format='', binary=None, delimiter=',', precision=12, flush=False, source=sys.stdin, target=sys.stdout ):
    if not isinstance( s, struct ): raise Exception( "expected '{}', got '{}'".format( str( struct ), repr( s ) ) )
    self.struct = s
    if binary:
      if fields or format: warnings.warn( "fields and format are ignored when binary keyword is set; default fields and format are used" )
      fields = ''
      format = '' if binary == False else ','.join( type if isinstance( type, basestring ) else numpy.dtype( type ).str for type in self.struct.types )
    self.fields = tuple( sum( map( lambda name: self.struct.shorthand.get( name ) or [name], fields.split(',') ), [] ) ) if fields else self.struct.fields
    if not set( self.fields ).issuperset( self.struct.fields ):
      raise Exception( "expected field(s) '{}' not found in supplied fields '{}'".format( ','.join( set( self.struct.fields ) - set( self.fields ) ), fields ) )
    duplicates = [ field for field in self.fields if field and self.fields.count( field ) > 1 ]
    if duplicates: raise Exception( "fields '{}' have duplicates in '{}'".format( ','.join( duplicates ), ','.join( self.fields ) ) )
    self.binary = format is not ''
    self.delimiter = delimiter
    self.flush = flush
    self.precision = precision
    self.source = source
    self.target = target
    if self.binary:
      self.dtype = numpy.dtype( format )
      if len( self.fields ) != len( self.dtype.names ): raise Exception( "expected same number of fields and format types, got '{}' and '{}'".format( ','.join( self.fields ), format ) )
    else:
      if self.fields == self.struct.fields:
        self.dtype = self.struct.flat_dtype
      else:
        struct_type_of_field = dict( zip( self.struct.fields, self.struct.types ) )
        names = [ 'f' + str( i ) for i in range( len( self.fields ) ) ]
        types = [ struct_type_of_field.get( name ) or 'S' for name in self.fields ]
        self.dtype = numpy.dtype( zip( names, types ) )
    self.size = max( 1, stream.buffer_size_in_bytes / self.dtype.itemsize )
    self.ascii_converters = comma.csv.time.ascii_converters( shape_unrolled_types_of_flat_dtype( self.dtype ) )
    if self.fields == self.struct.fields:
        self.reshaped_dtype = None
    else:
      names = [ 'f' + str( self.fields.index( name ) ) for name in self.struct.fields ]
      formats = [ self.dtype.fields[name][0] for name in names ]
      offsets = [ self.dtype.fields[name][1] for name in names ]
      self.reshaped_dtype = numpy.dtype( dict( names=names, formats=formats, offsets=offsets ) )

  def iter( self, size=None ):
    while True:
      s = self.read( size )
      if s is None: break
      yield s

  def read( self, size=None ):
    size = self.size if size is None else size
    if size <= 0:
      if self.source == sys.stdin: raise Exception( "expected positive size when stream source is stdin, got {}".format( size ) )
      size = -1 if self.binary else None
    if self.binary:
      data = numpy.fromfile( self.source, dtype=self.dtype, count=size )
    else:
      with warnings.catch_warnings():
        warnings.simplefilter( 'ignore' )
        self.ascii_input_buffer = ''.join( itertools.islice( self.source, size ) )
        data = numpy.loadtxt( StringIO( self.ascii_input_buffer ), dtype=self.dtype , delimiter=self.delimiter, converters=self.ascii_converters, ndmin=1 )
    if data.size == 0: return None
    if self.reshaped_dtype:
      return numpy.array( numpy.ndarray( data.shape, self.reshaped_dtype, data, strides=data.itemsize ).tolist(), dtype=self.struct.flat_dtype ).view( self.struct )
    else:
      return data.view( self.struct )

  def numpy_scalar_to_string( self, scalar ):
    if scalar.dtype.char in numpy.typecodes['AllInteger']: return str( scalar )
    elif scalar.dtype.char in numpy.typecodes['Float']: return "{scalar:.{precision}g}".format( scalar=scalar, precision=self.precision )
    elif scalar.dtype.char in numpy.typecodes['Datetime'] : return comma.csv.time.from_numpy( scalar )
    elif scalar.dtype.char in 'S': return scalar
    else: raise Exception( "conversion to string for numpy type '{}' is not implemented".format( repr( scalar.dtype ) ) )

  def write( self, s ):
    if s.dtype != self.struct.dtype:
      raise Exception( "expected object of dtype '{}', got '{}'".format( str( self.struct.dtype ), repr( s.dtype ) ) )
    if self.binary:
      s.tofile( self.target )
    else:
      for _ in s.view( self.struct.unrolled_flat_dtype ):
        print >> self.target, self.delimiter.join( map( self.numpy_scalar_to_string, _ ) )
    if self.flush: self.target.flush()
