#AUTOGENERATED! DO NOT EDIT! File to edit: dev/08_vision_augment.ipynb (unless otherwise specified).

__all__ = ['RandTransform', 'PILFlip', 'PILDihedral', 'clip_remove_empty', 'mask_tensor', 'masked_uniform',
           'rotate_mat', 'find_coeffs', 'apply_perspective', 'Warp']

from ..imports import *
from ..test import *
from ..core import *
from ..data.pipeline import *
from ..data.source import *
from ..data.core import *
from .core import *
from ..data.external import *
from ..notebook.showdoc import show_doc

from torch import stack, zeros_like as t0, ones_like as t1
from torch.distributions.bernoulli import Bernoulli

@docs
class RandTransform(Transform):
    "A transform that randomize its state at each `__call__`, only applied on the training set"
    filt=0
    def __init__(self, encodes=None, decodes=None, randomize=None, p=1.):
        self.p = p
        if randomize is not None: self.randomize=randomize
        super().__init__(encodes, decodes)

    def randomize(self, b): self.do = random.random() < self.p

    def __call__(self, b, filt=None, **kwargs):
        self.randomize(b) #Randomize before calling
        if not getattr(self, 'do', True): return b
        return super().__call__(b, filt=filt, **kwargs)

    _docs = dict(randomize="Randomize the state for input `b`")

def _minus_axis(x, axis):
    x[...,axis] = -x[...,axis]
    return x

class PILFlip(RandTransform):
    "Randomly flip with probability `p`"
    def __init__(self, p=0.5): self.p = p
    def encodes(self, x:PILImage):    return x.transpose(PIL.Image.FLIP_LEFT_RIGHT)
    def encodes(self, x:TensorPoint): return _minus_axis(x, 1)
    def encodes(self, x:TensorBBox):
        bb,lbl = x
        bb = _minus_axis(bb.view(-1,2), 1)
        return (bb.view(-1,4),lbl)

class PILDihedral(RandTransform):
    "Applies any of the eight dihedral transformations with probability `p`"
    def __init__(self, p=0.5, draw=None): self.p,self.draw = p,draw
    def randomize(self, b):
        super().randomize(b)
        if self.draw is None: self.idx = random.randint(0,7)
        else: self.idx = self.draw() if isinstance(self.draw, Callable) else self.draw

    def encodes(self, x:PILImage): return x if self.idx==0 else x.transpose(self.idx-1)
    def encodes(self, x:TensorPoint):
        if self.idx in [1, 3, 4, 7]: x = _minus_axis(x, 1)
        if self.idx in [2, 4, 5, 7]: x = _minus_axis(x, 0)
        if self.idx in [3, 5, 6, 7]: x = x.flip(1)
        return x

    def encodes(self,  x:TensorBBox):
        pnts = self._get_func(self.encodes, TensorPoint)(x[0].view(-1,2)).view(-1,2,2)
        tl,dr = pnts.min(dim=1)[0],pnts.max(dim=1)[0]
        return [torch.cat([tl, dr], dim=1), x[1]]

def clip_remove_empty(bbox, label):
    "Clip bounding boxes with image border and label background the empty ones."
    bbox = torch.clamp(bbox, -1, 1)
    empty = ((bbox[...,2] - bbox[...,0])*(bbox[...,3] - bbox[...,1]) < 0.)
    if isinstance(label, torch.Tensor): label[empty] = 0
    else: label = [0 if m else l for l,m in zip(label,empty)]
    return [bbox, label]

def mask_tensor(x, p=0.5, neutral=0.):
    if p==1.: return x
    if neutral != 0: x.add_(-neutral)
    mask = x.new_empty(*x.size()).bernoulli_(p)
    x.mul_(mask)
    return x.add_(neutral) if neutral != 0 else x

def masked_uniform(x, a, b, *sz, p=0.5, neutral=0.):
    return mask_tensor(x.new_empty(*sz).uniform_(a,b), p=p, neutral=neutral)

def rotate_mat(x, max_deg=10, p=0.5, draw=None):
    thetas = masked_uniform(x, -max_deg, max_deg, x.size(0), p=p) * math.pi/180
    return affine_mat(thetas.cos(), thetas.sin(), t0(thetas),
                     -thetas.sin(), thetas.cos(), t0(thetas),
                      t0(thetas),   t0(thetas),   t1(thetas))

def find_coeffs(p1, p2):
    matrix = []
    p = p1[:,0,0]
    #The equations we'll need to solve.
    for i in range(p1.shape[1]):
        matrix.append(stack([p2[:,i,0], p2[:,i,1], t1(p), t0(p), t0(p), t0(p), -p1[:,i,0]*p2[:,i,0], -p1[:,i,0]*p2[:,i,1]]))
        matrix.append(stack([t0(p), t0(p), t0(p), p2[:,i,0], p2[:,i,1], t1(p), -p1[:,i,1]*p2[:,i,0], -p1[:,i,1]*p2[:,i,1]]))
    #The 8 scalars we seek are solution of AX = B
    A = stack(matrix).permute(2, 0, 1)
    B = p1.view(p1.shape[0], 8, 1)
    return torch.solve(B,A)[0]

def apply_perspective(coords, coeffs):
    sz = coords.shape
    coords = coords.view(sz[0], -1, 2)
    coeffs = torch.cat([coeffs, t1(coeffs[:,:1])], dim=1).view(coeffs.shape[0], 3,3)
    coords = coords @ coeffs[...,:2].transpose(1,2) + coeffs[...,2].unsqueeze(1)
    coords.div_(coords[...,2].unsqueeze(-1))
    return coords[...,:2].view(*sz)

class Warp():
    def __init__(self, magnitude=0.2, p=0.5):
        self.coeffs,self.magnitude,self.p = None,magnitude,p

    def randomize(self, x):
        up_t = masked_uniform(x, -self.magnitude, self.magnitude, x.size(0), p=self.p)
        lr_t = masked_uniform(x, -self.magnitude, self.magnitude, x.size(0), p=self.p)
        orig_pts = torch.tensor([[-1,-1], [-1,1], [1,-1], [1,1]], dtype=x.dtype, device=x.device)
        self.orig_pts = orig_pts.unsqueeze(0).expand(x.size(0),4,2)
        targ_pts = stack([stack([-1-up_t, -1-lr_t]), stack([-1+up_t, 1+lr_t]),
                          stack([ 1+up_t, -1+lr_t]), stack([ 1-up_t, 1-lr_t])])
        self.targ_pts = targ_pts.permute(2,0,1)

    def __call__(self, x, invert=False):
        coeffs = find_coeffs(self.targ_pts, self.orig_pts) if invert else find_coeffs(self.orig_pts, self.targ_pts)
        return apply_perspective(x, coeffs)