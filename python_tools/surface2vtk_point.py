#!/usr/bin/env python
# -*- coding: utf-8 -*-

'''
surface2vtk_point.py

Generate VTK animations from a NetCDF database of surface wavefield 
created by AxiSEM3D (named axisem3d_surface.nc by the solver). Data
are presented on discretised vertices.

To see usage, type
python surface2vtk_point.py -h
'''
    
################### PARSER ###################
aim = '''Generate VTK animations from a NetCDF database of surface wavefield 
created by AxiSEM3D (named axisem3d_surface.nc by the solver). Data
are presented on discretised vertices.'''

notes = '''Parallelise data processing using --nporc option.
Animate the VKT files with Paraview.
 
'''

import argparse
from argparse import RawTextHelpFormatter
parser = argparse.ArgumentParser(description=aim, epilog=notes, 
                                 formatter_class=RawTextHelpFormatter)
parser.add_argument('-i', '--input', dest='in_surface_nc', 
                    action='store', type=str, required=True,
                    help='NetCDF database of surface wavefield\n' + 
                         'created by AxiSEM3D <required>')
parser.add_argument('-o', '--output', dest='out_vtk', 
                    action='store', type=str, required=True,
                    help='directory to store the vtk files\n' +
                         '<required>') 
parser.add_argument('-s', '--spatial_sampling', dest='spatial_sampling', 
                    action='store', type=float, required=True,
                    help='spatial sampling on surface (km)\n' +
                         '<required>') 
parser.add_argument('-m', '--min_dist', dest='min_dist', 
                    action='store', type=float, default=0.,
                    help='minimum distance (deg); default = 0')
parser.add_argument('-M', '--max_dist', dest='max_dist', 
                    action='store', type=float, default=180.,
                    help='maximum distance (deg); default = 180')                          
parser.add_argument('-t', '--tstart', dest='tstart', 
                    action='store', type=float, required=True,
                    help='start time of animation (sec)\n' +
                         '<required>') 
parser.add_argument('-d', '--time_interval', dest='time_interval', 
                    action='store', type=float, required=True,
                    help='time interval between snapshots (sec)\n' +
                         '<required>') 
parser.add_argument('-n', '--nsnapshots', dest='nsnapshots',
                    action='store', type=int, required=True,
                    help='number of snapshots <required>')
parser.add_argument('-p', '--nproc', dest='nproc', action='store', 
                    type=int, default=1, 
                    help='number of processors; default = 1')
parser.add_argument('-v', '--verbose', dest='verbose', action='store_true', 
                    help='verbose mode')        
args = parser.parse_args()

################### PARSER ###################

import numpy as np
from netCDF4 import Dataset
import pyvtk, os, shutil
from multiprocessing import Pool
import time

###### read surface database
if args.verbose:
    clock0 = time.clock()
    print('Reading global parameters...')
nc_surf = Dataset(args.in_surface_nc, 'r', format='NETCDF4')
# global attribute
srclat = nc_surf.source_latitude
srclon = nc_surf.source_longitude
srcdep = nc_surf.source_depth
srcflat = nc_surf.source_flattening
surfflat = nc_surf.surface_flattening
r_outer = nc_surf.radius
# time
var_time = nc_surf.variables['time_points']
nstep = len(var_time)
assert nstep > 0, 'Zero time steps'
t0 = var_time[0]
solver_dtype = var_time.datatype
# theta
var_theta = nc_surf.variables['theta']
nele = len(var_theta)
# GLL and GLJ
var_GLL = nc_surf.variables['GLL']
var_GLJ = nc_surf.variables['GLJ']
nPntEdge = len(var_GLL)
if args.verbose:
    elapsed = time.clock() - clock0
    print('Reading global parameters done, ' + 
          '%f sec elapsed.\n' % (elapsed))

###### surface sampling
if args.verbose:
    clock0 = time.clock()
    print('Sampling surface...')
ndist = int(np.radians(args.max_dist - args.min_dist) * r_outer / \
         (args.spatial_sampling * 1e3)) + 1
dists = np.linspace(np.radians(args.min_dist), np.radians(args.max_dist), 
                    num=ndist, endpoint=True)
azims = []
nazim = np.zeros(ndist, dtype=int)
for idist, dist in enumerate(dists):
    r = r_outer * np.sin(dist)
    nazim[idist] = int(2. * np.pi * r / (args.spatial_sampling * 1e3)) + 1
    azims.append(np.linspace(0, 2. * np.pi, num=nazim[idist], endpoint=False))
nstation = np.sum(nazim)
if args.verbose:
    elapsed = time.clock() - clock0
    print('    Number of distances: %d' % (ndist))
    print('    Number of sampling points: %d' % (nstation))
    print('Sampling surface done, ' + 
          '%f sec elapsed.\n' % (elapsed))
    
###### xyz
if args.verbose:
    print('Computing xyz of points...')    
dist_azim = np.zeros((nstation, 2))
istart = 0
for idist, dist in enumerate(dists):
    dist_azim[istart:(istart + nazim[idist]), 0] = dist
    for iazim, azim in enumerate(azims[idist]):
        dist_azim[istart + iazim, 1] = azim
    istart += nazim[idist]
x = np.sin(dist_azim[:, 0]) * np.cos(dist_azim[:, 1])
y = np.sin(dist_azim[:, 0]) * np.sin(dist_azim[:, 1])
z = np.cos(dist_azim[:, 0])
vtk_points = pyvtk.UnstructuredGrid(list(zip(x, y, z)), range(nstation))
if args.verbose:
    elapsed = time.clock() - clock0
    print('Computing xyz of points done, ' + 
          '%f sec elapsed.\n' % (elapsed))

###### prepare theta
def interpLagrange(target, lbases):
    nrow, ncol = lbases.shape
    results = np.zeros((nrow, ncol))
    target_dgr = np.tile(np.array([target]).T, (1, ncol - 1))
    for dgr in np.arange(0, ncol):
        lbases_dgr = np.tile(lbases[:, [dgr]], (1, ncol - 1))
        lbases_sub = np.delete(lbases, dgr, axis=1)
        results[:, dgr] = np.prod(target_dgr - lbases_sub, axis=1) / \
                          np.prod(lbases_dgr - lbases_sub, axis=1)
    return results

if args.verbose:
    clock0 = time.clock()
    print('Locating points in distance...')
# locate element
max_theta = np.amax(var_theta, axis=1)
eleTags = np.searchsorted(max_theta, dists)
# compute weights
lbases = np.tile(var_GLL, (ndist, 1))
lbases[0, :] = var_GLJ[:]
lbases[-1, :] = var_GLJ[:]
theta_bounds = var_theta[eleTags, :]
etas = (dists - theta_bounds[:, 0]) / (theta_bounds[:, 1] - theta_bounds[:, 0]) * 2. - 1.
weights = interpLagrange(etas, lbases)
if args.verbose:
    elapsed = time.clock() - clock0
    print('Locating points in distance done, ' + 
          '%f sec elapsed.\n' % (elapsed))    

###### prepare time steps
if args.verbose:
    clock0 = time.clock()
    print('Preparing timesteps...')
if nstep == 1:
    steps = np.array([0])
    dt = 0.
else:
    dt = var_time[1] - t0
    istart = max(int(round((args.tstart - t0) / dt)), 0)
    dtsteps = max(int(round(args.time_interval / dt)), 1)
    iend = min(istart + dtsteps * (args.nsnapshots - 1) + 1, nstep)
    steps = np.arange(istart, iend, dtsteps)
if args.verbose:
    elapsed = time.clock() - clock0
    print('    Number of snapshots: %d' % (len(steps)))
    print('Preparing timesteps done, ' + 
          '%f sec elapsed.\n' % (elapsed))

###### IO
# close input    
nc_surf.close()
# create output directory
try:
    os.makedirs(args.out_vtk)
except OSError:
    pass

def write_vtk(iproc):
    if args.nproc == 1:
        nc_surf_local = Dataset(args.in_surface_nc, 'r', format='NETCDF4')
        iproc = 0
    else:
        # copy netcdf file for parallel access
        tempnc = args.out_vtk + '/surface_temp.nc' + str(iproc)
        shutil.copy(args.in_surface_nc, tempnc)
        nc_surf_local = Dataset(tempnc, 'r', format='NETCDF4')

    # write vtk
    if args.verbose and iproc == 0:
        clock0 = time.clock()
        print('Generating snapshot...')
    for it, istep in enumerate(steps):
        if (it % args.nproc != iproc): 
            continue
        disp = np.zeros((nstation, 3))
        istation = 0
        for idist, dist in enumerate(dists):
            fourier_r = nc_surf_local.variables['edge_' + str(eleTags[idist]) + 'r'][istep, :]
            fourier_i = nc_surf_local.variables['edge_' + str(eleTags[idist]) + 'i'][istep, :]
            fourier = fourier_r[:] + fourier_i[:] * 1j
            nu_p_1 = int(len(fourier) / nPntEdge / 3)
            wdotf = np.zeros((3, nu_p_1), dtype=fourier.dtype)
            for idim in np.arange(0, 3):
                start = idim * nPntEdge * nu_p_1
                end = idim * nPntEdge * nu_p_1 + nPntEdge * nu_p_1
                fmat = fourier[start:end].reshape(nPntEdge, nu_p_1)
                wdotf[idim] = weights[idist].dot(fmat)
            for iazim, azim in enumerate(azims[idist]):
                exparray = 2. * np.exp(np.arange(0, nu_p_1) * 1j * azim)
                exparray[0] = 1.
                spz = wdotf.dot(exparray).real
                disp[istation, 0] = spz[0] * np.cos(dist) - spz[2] * np.sin(dist)
                disp[istation, 1] = spz[1]
                disp[istation, 2] = spz[0] * np.sin(dist) + spz[2] * np.cos(dist)
                istation += 1
        vtk = pyvtk.VtkData(vtk_points,
            pyvtk.PointData(pyvtk.Vectors(disp, name='disp_RTZ')),
            'surface animation')
        vtk.tofile(args.out_vtk + '/surface_vtk_point.' + str(it) + '.vtk', 'binary')
        if args.verbose:
            print('    Done with snapshot t = %f s; tstep = %d / %d; iproc = %d' \
                % (istep * dt + t0, it + 1, len(steps), iproc))
    # close
    nc_surf_local.close()
    
    # remove temp nc
    if args.nproc > 1:
        os.remove(tempnc)
    
    if args.verbose and iproc == 0:
        elapsed = time.clock() - clock0
        print('Generating snapshots done, ' + 
              '%f sec elapsed.' % (elapsed))

# write_vtk in parallel
args.nproc = max(args.nproc, 1)
if args.nproc == 1:
    write_vtk(0)
else:
    with Pool(args.nproc) as p:
        p.map(write_vtk, range(0, args.nproc))
        

