import re
import sys

from mapnik_utils.version_adapter import Mapnik

mapnik = Mapnik()

# mapnik_utils
from mapnik_utils.projection import EasyProjection

if not hasattr(mapnik,'ProjTransform'):
    from compatibility import ProjTransform

BoostPythonMetaclass = mapnik.Coord.__class__
                
class _injector(object):
    class __metaclass__(BoostPythonMetaclass):
        def __init__(self, name, bases, dict):
            for b in bases:
                if type(b) not in (self, type):
                    for k,v in dict.items():
                        setattr(b,k,v)
            return type.__init__(self, name, bases, dict)

if not hasattr(mapnik,'Box2d'):
    mapnik.Box2d = mapnik.Envelope

class _Map(mapnik.Map,_injector):

    def set_easy_srs(self,srs):
        self.srs = EasyProjection(srs).params()

    @property
    def proj_obj(self):
        return EasyProjection(self.srs)

    def lon_lat_bbox(self):
        return self.envelope().forward(self.proj_obj,EasyProjection(4326))

    def find_layer(self,name):
        lyr = [l for l in self.layers if l.name.lower() == name.lower()]
        if not lyr:
            raise ValueError('Layer "%s" not found, available layers are: ["%s"]' % (name,', '.join(self.layer_names())))
        return lyr[0]
    
    def layer_names(self):
        return [l.name for l in self.layers]

    def active_layers(self):
        return [l.name for l in self.layers if l.active]

    def zoom_to_layer(self,layer):
        layer = self.find_layer(layer)
        layer_box = layer.envelope()
        box = layer_box.forward(layer.proj_obj,self.proj_obj)
        self.zoom_to_box(box)

    def lon_lat_layers_bounds(self):
        return self.layers_bounds().forward(self.proj_obj,EasyProjection(4326))

    def layers_bounds(self):
        new_box = None
        if len(self.layers):
            first = self.layers[0]
            new_box = None
            try:
                new_box = first.envelope().forward(first.proj_obj,self.proj_obj)
            except RuntimeError:
                # try clipping layer extent to map
                new_box = self.envelope().forward(self.proj_obj,first.proj_obj)
                new_box.clip(first.envelope())
            for layer in self.layers:
                layer_box = layer.envelope()
                box = None
                try:
                    box = layer_box.forward(layer.proj_obj,self.proj_obj)
                except RuntimeError:
                    # try clipping layer extent to map
                    box = self.envelope().forward(self.proj_obj,first.proj_obj)
                    box.clip(layer_box)
                new_box.expand_to_include(box)
        return new_box
    
    def zoom_to_layers(self,layers):
        first = self.find_layer(layers[0])
        new_box = first.envelope().forward(first.proj_obj,self.proj_obj)
        for lyr in layers:
            layer = self.find_layer(lyr)
            layer_box = layer.envelope()
            box = layer_box.forward(layer.proj_obj,self.proj_obj)
            new_box.expand_to_include(box)
        self.zoom_to_box(new_box)

    def zoom_to_level(self,level):
        c = self.layers_bounds().center()
        self.set_center_and_zoom(c.x,c.y,level=level,geographic=self.proj_obj.geographic)
    
    def max_resolution(self):
        #self.zoom_max()
        map_w,map_h = self.envelope().width(),self.envelope().height()
        return max(map_w / self.width, map_h / self.height)

    def get_scales(self,number):
        max_res = self.max_resolution()
        return [max_res / 2 ** i for i in range(int(number))]        

    def get_scale_for_zoom_level(self,level):
        return self.get_scales(level+1)[level]
    
    # http://trac.mapnik.org/browser/trunk/src/map.cpp#L245
    def set_center_and_zoom(self,lon,lat,level=0,geographic=True):
        coords = mapnik.Coord(lon,lat)
        if geographic and not self.proj_obj.geographic:
            wgs_84 = mapnik.Projection('+init=epsg:4326')
            coords = coords.forward(wgs_84,self.proj_obj)
        w,h = self.width, self.height
        res = self.get_scale_for_zoom_level(level) 
        box = mapnik.Box2d(coords.x - 0.5 * w * res,
                    coords.y - 0.5 * h * res, 
                    coords.x + 0.5 * w * res, 
                    coords.y + 0.5 * h * res)
        self.zoom_to_box(box) 

    def set_center_and_radius(self,lon,lat,radius=None,geographic=True):
        coords = mapnik.Coord(lon,lat)
        box = mapnik.Box2d(coords.x - radius,
                      coords.y - radius,
                      coords.x + radius,
                      coords.y + radius)
        if geographic and not self.proj_obj.geographic:
            wgs_84 = mapnik.Projection('+init=epsg:4326')
            box = box.forward(wgs_84,self.proj_obj)
        self.zoom_to_box(box)

    def zoom_max(self):
        max_extent = mapnik.Box2d(-179.99999694572804,-85.0511285163245,179.99999694572804,85.0511287798066)
        if not self.proj_obj.geographic:
            wgs_84 = mapnik.Projection('+init=epsg:4326')
            max_extent = max_extent.forward(wgs_84,self.proj_obj)
        self.zoom_to_box(max_extent)

    def activate_layers(self,names):
        self.select_layers(names,remove=False)

    def select_layers(self,names,remove=True):
        disactivated = []
        selected = []
        if not isinstance(names,list):
            names = [names]
        for lyr in self.layers:
            if not lyr.name in names and remove:
                lyr.active = False
                disactivated.append(lyr.name)
            else:
                lyr.active = True
                selected.append(lyr.name)
        return selected, disactivated 
    
    def intersecting_layers(self):
        lyrs = []
        for layer in self.layers:
            layer_box = None
            try:
                layer_box = layer.envelope().forward(layer.proj_obj,self.proj_obj)
            except RuntimeError:
                # try clipping layer extent to map
                layer_box = self.envelope().forward(self.proj_obj,layer.proj_obj)
                layer_box.clip(layer.envelope())
            if layer_box.intersects(self.envelope()):
                #layer.active_rules = layer.active_rules(self)
                lyrs.append(layer)
        return lyrs

    def to_wld(self, x_rotation=0.0, y_rotation=0.0):
        """
        Outputs an ESRI world file that can be used to load the resulting
        image as a georeferenced raster in a variety of gis viewers.
        
        '.wld' is the most common extension used, but format-specific extensions
        are also looked for by some software, such as '.tfw' for tiff and '.pgw' for png
        
        A world file file is a plain ASCII text file consisting of six values separated
        by newlines. The format is: 
            pixel X size
            rotation about the Y axis (usually 0.0)
            rotation about the X axis (usually 0.0)
            pixel Y size (negative when using North-Up data)
            X coordinate of upper left pixel center
            Y coordinate of upper left pixel center
         
        Info from: http://gdal.osgeo.org/frmt_various.html#WLD
        """
        extent = self.envelope()
        pixel_x_size = (extent.maxx - extent.minx)/self.width
        pixel_y_size = (extent.maxy - extent.miny)/self.height
        upper_left_x_center = extent.minx + 0.5 * pixel_x_size + 0.5 * x_rotation
        upper_left_y_center = extent.maxy + 0.5 * (pixel_y_size*-1) + 0.5 * y_rotation
        # http://trac.osgeo.org/gdal/browser/trunk/gdal/gcore/gdal_misc.cpp#L1296
        wld_string = '''%.10f\n%.10f\n%.10f\n-%.10f\n%.10f\n%.10f\n''' % (
                pixel_x_size, # geotransform[1] - width of pixel
                y_rotation, # geotransform[4] - rotational coefficient, zero for north up images.
                x_rotation, # geotransform[2] - rotational coefficient, zero for north up images.
                pixel_y_size, # geotransform[5] - height of pixel (but negative)
                upper_left_x_center, # geotransform[0] - x offset to center of top left pixel
                upper_left_y_center # geotransform[3] - y offset to center of top left pixel.
            )
        return wld_string
                  
class _Layer(mapnik.Layer,_injector):

    @property
    def proj_obj(self):
        return EasyProjection(self.srs)

    def set_srs_by_srid(self,srid):
        self.srs = EasyProjection(srid).params()
    
    def active_rules(self,map):
        rules = []
        for style in self.styles:
            sty_obj = map.find_style(style)
            for rule in sty_obj.rules:
                if rule.active(map.scale_denominator()):
                    rules.append({'name':rule.name,'parent':style,'filter':str(rule.filter)})
        return rules


class _Coord(mapnik.Coord,_injector):
    def forward(self,from_prj,to_prj):
        trans = mapnik.ProjTransform(from_prj,to_prj)
        return trans.forward(self)

class _Box2d(mapnik.Box2d,_injector):
    def forward(self,from_prj,to_prj):
        trans = mapnik.ProjTransform(from_prj,to_prj)
        return trans.forward(self)

if __name__ == '__main__':
    import doctest
    doctest.testmod()