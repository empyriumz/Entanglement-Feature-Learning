''' Entanglement Feature Learning '''
# package required: numpy, theano
import numpy
import theano
import theano.tensor as T
import os
numpy.set_printoptions(formatter={'float': '{: 0.3f}'.format})
''' Orthogonalize
by QR decomposition (column-wise) '''
from theano.tensor import as_tensor_variable
from theano.gof import Op, Apply
class Orthogonalize(Op): # define theano Op
    _numpyqr = staticmethod(numpy.linalg.qr) # static numpy qr
    __props__ = () # no properties for this Op
    # creates an Apply node
    def make_node(self, x):
        x = as_tensor_variable(x)
        assert x.ndim == 2, "The input of qr function should be a matrix."
        y = theano.tensor.matrix(dtype=x.dtype)
        return Apply(self, [x], [y])
    # Phython implementation
    def perform(self, node, inputs, outputs):
        (x,) = inputs
        (y,) = outputs
        assert x.ndim == 2, "The input of qr function should be a matrix."
        q, r = self._numpyqr(x,'reduced') # QR decomposition
        # d = diagonal of r as vector
        if r.shape[0] < r.shape[1]:
            d = r[:, 0]
        else:
            d = r[0]
        d.strides = (r.strides[0] + r.strides[1],)
        # column-wise multiply d to q
        q *= d
        # if q columns < x columns, pad zero columns from the right
        if q.shape[1] < x.shape[1]:
            q = numpy.pad(q, ((0,0),(0,x.shape[1]-q.shape[1])),'constant')
        y[0] = q # set output to q
    # string representation
    def __str__(self):
        return 'Orthogonalize'
# alias
orth = Orthogonalize()
''' RBM (method CD)
* unbiased, spins take values in {-1,+1}.
Nv::int: number of visible units
Nh::int: number of hidden units
W::T.matrix: theano shared weight matrix for a kernel in CDBN.
             None for standalone RBMs.
type::string: type of RBM in a DBM, can be 'default', 'bottom', 'top', 'intermediate' 
Markov_steps::int: number of Markov steps in Gibbs sampling
'''
class RBM(object):
    ''' RMB Constructor '''
    def __init__(self,
            Nv = 1, Nh = 1, W = None, input = None, type = 'default',
            numpy_rng = None, theano_rng = None):
        # set number of units
        self.Nv = Nv
        self.Nh = Nh
        # set random number generators
        self.init_rng(numpy_rng, theano_rng)
        # symbolize weight matrix
        self.init_W(W)
        self.type = type # set RBM type
        self.Markov_steps = 15 # set Markov steps
        # symbolize visible input for RBM
        if input is None:
            self.input = T.matrix('RBM.input',dtype=theano.config.floatX)
        else:
            self.input = input
        self.batch_size, _ = self.input.shape # set batch size from input
        self.output = self.propup(self.input)
        # symbolize rates
        self.lr = T.scalar('lr',dtype=theano.config.floatX)
        self.fr = T.scalar('fr',dtype=theano.config.floatX)
        self.orth_on = T.scalar('orth_on',dtype='int8')
        # build learning function for default RBM 
        if self.type is 'default':
            cost, updates = self.get_cost_updates()
            self.learn = theano.function(
                [self.input, 
                 theano.In(self.lr, value=0.1),
                 theano.In(self.fr, value=0.),
                 theano.In(self.orth_on, value=False)],
                cost, updates = updates, name = 'RBM.learn')
    # initialize random number generator
    def init_rng(self, numpy_rng, theano_rng):
        # set numpy random number generator
        if numpy_rng is None:
            self.numpy_rng = numpy.random.RandomState()
        else:
            self.numpy_rng = numpy_rng
        # set theano random number generator
        if theano_rng is None:
            seed = self.numpy_rng.randint(2**30)
            self.theano_rng = T.shared_randomstreams.RandomStreams(seed)
        else:
            self.theano_rng = theano_rng
    # construct initial weight matrix variable
    def init_W(self, W):
        def build_W(mode):
            Nv, Nh = self.Nv, self.Nh
            if mode == 'random': # random initialization
                W_raw = self.numpy_rng.uniform(low=0., high=1., size=(Nv, Nh))
                W_raw *= numpy.sqrt(6./(Nv+Nh))
            elif mode == 'local': # local initialization
                vx = numpy.arange(0.5/Nv,1.,1./Nv)
                hx = numpy.arange(0.5/Nh,1.,1./Nh)
                hxs, vxs = numpy.meshgrid(hx, vx)
                W_raw = numpy.exp(-(hxs-vxs)**2*(5*Nh**2))
            elif mode == 'localrand':
                vx = numpy.arange(0.5/Nv,1.,1./Nv)
                hx = numpy.arange(0.5/Nh,1.,1./Nh)
                hxs, vxs = numpy.meshgrid(hx, vx)
                W_raw = numpy.exp(-(hxs-vxs)**2*(5*Nh**2))
                W_raw *= self.numpy_rng.uniform(low=0., high=2., size=(Nv, Nh))
            elif mode == 'multiworld': # multiworld initialization
                hw = numpy.exp(-numpy.linspace(0.,1.,Nh))
                W_raw = numpy.repeat([hw],Nv,axis=0)
            else: # unknown mode
                raise ValueError("%s is not a known mode for weight matrix initializaiton. Use 'random', 'local', 'multiworld' to initialize weight matrix."%mode)
            W_mat = numpy.asarray(W_raw, dtype=theano.config.floatX)
            return theano.shared(value=W_mat, name='W', borrow=True) 
        if W is None:
            self.W = build_W('localrand')
        elif isinstance(W, str):
            self.W = build_W(W)
        else: # assuming W is a theano shared matrix
            self.W = W
            # in this case, Nv, Nh are override by the shape of W
            self.Nv, self.Nh = W.get_value().shape
    ''' RBM Physical Dynamics '''
    # Propagate visible configs upwards to hidden configs
    def propup(self, v_samples):
        local_fields = T.dot(v_samples, self.W) # local fields for hiddens
        # double local field for bottom, intermediate
        if self.type in {'bottom', 'intermediate'}:
            local_fields *= 2
        h_means = T.tanh(local_fields)
        return h_means
    # Infer hidden samples given visible samples
    def sample_h_given_v(self, v_samples):
        h_means = self.propup(v_samples) # expectations of hiddens
        h_probs = (h_means+1)/2 # probabilities for hiddens
        # get a sample of hiddens for once (n=1) with probability p
        h_samples = self.theano_rng.binomial(
            size=h_means.shape, n=1, p=h_probs,
            dtype=theano.config.floatX)*2-1
        return h_means, h_samples
    # Propagate hidden configs downwards to visible configs
    def propdown(self, h_samples):
        local_fields = T.dot(h_samples, self.W.T) # local fields for visibles
        # double local field for top, intermediate
        if self.type in {'top', 'intermediate'}:
            local_fields *= 2
        v_means = T.tanh(local_fields)
        return v_means
    # Infer visible samples given hidden samples
    def sample_v_given_h(self, h_samples):
        v_means = self.propdown(h_samples) # expectations of visibles
        v_probs = (v_means+1)/2 # probabilities for visibles
        # get a sample of visibles for once (n=1) with probability p
        v_samples = self.theano_rng.binomial(
            size=v_means.shape, n=1, p=v_probs,
            dtype=theano.config.floatX)*2-1
        return v_means, v_samples
    # One step of Gibbs sampling from hiddens
    def gibbs_hvh(self, h0_samples):
        v1_means, v1_samples = self.sample_v_given_h(h0_samples)
        h1_means, h1_samples = self.sample_h_given_v(v1_samples)
        return [v1_means, v1_samples, h1_means, h1_samples]
    # One step of Gibbs sampling from visibles
    def gibbs_vhv(self, v0_samples):
        h1_means, h1_samples = self.sample_h_given_v(v0_samples)
        v1_means, v1_samples = self.sample_v_given_h(h1_samples)
        return [h1_means, h1_samples, v1_means, v1_samples]
    ''' Implement CD-k '''
    # Get cost and updates
    def get_cost_updates(self):
        # CD, generate new chain start from input
        h0_means, h0_samples = self.sample_h_given_v(self.input)
        # relax to equilibrium by Gibbs sampling
        ([v_means_chain, v_samples_chain, h_means_chain, h_samples_chain],
         updates) = theano.scan(
            self.gibbs_hvh,
            outputs_info=[None,None,None,h0_samples],
            n_steps=self.Markov_steps, # k = Markov steps
            name='gibbs_hvh')
        # take chain end
        vf_samples = v_samples_chain[-1]
        vf_means = v_means_chain[-1]
        hf_means = h_means_chain[-1]
        # compute negative gradient on weights
        dW = (T.dot(self.input.T,h0_means) - T.dot(vf_samples.T,hf_means))/self.batch_size
        # if epoch == 0, turn on orthogonalization mode
        # orthogonalize dW to prevent learning the same feature
        dW = theano.ifelse.ifelse(self.orth_on,orth(dW),dW) 
        # add W update rules to updates dict
        updates[self.W] = T.nnet.relu((1-self.fr)*self.W + self.lr*dW)
        # cost function: recontruction cross entropy (per visible)
        v0_probs = (self.input+1)/2 # initial visible probabilities
        vf_probs = (vf_means+1)/2   # final visible probabilities
        cost = T.mean(T.nnet.binary_crossentropy(vf_probs,v0_probs))
        return cost, updates
''' DBM
Nls::list of int: number of units in each layer
'''
class DBM(object):
    ''' DMB Constructor '''
    def __init__(self, Nls, numpy_rng = None, theano_rng = None):
        # set structral constants
        self.Nls = Nls
        self.L = len(Nls)-1 # number of RBMs
        assert self.L >= 2  # at least two layers of RBMs 
        # set random number generators
        self.init_rng(numpy_rng, theano_rng)
        # symbolize visible input for DBM
        self.input = T.matrix('DBM.input',dtype=theano.config.floatX)
        # build RBMs
        self.build_rbms()
    # initialize random number generator
    def init_rng(self, numpy_rng, theano_rng):
        # set numpy random number generator
        if numpy_rng is None:
            self.numpy_rng = numpy.random.RandomState()
        else:
            self.numpy_rng = numpy_rng
        # set theano random number generator
        if theano_rng is None:
            seed = self.numpy_rng.randint(2**30)
            self.theano_rng = T.shared_randomstreams.RandomStreams(seed)
        else:
            self.theano_rng = theano_rng
    # construct RBM layers
    def build_rbms(self):
        self.rbms = [] # initialize RBM container
        layer_input = self.input # layer input holder
        for l in range(self.L):
            Nv = self.Nls[l]   # visible units
            Nh = self.Nls[l+1] # hidden units
            # ascribe RBM type
            if l == 0:
                rbm_type = 'bottom'
            elif l == self.L-1:
                rbm_type = 'top'
            else:
                rbm_type = 'intermediate'
            # build RBM
            rbm = RBM(Nv, Nh, input=layer_input, type=rbm_type,
                    numpy_rng=self.numpy_rng, theano_rng=self.theano_rng)
            rbm.layer = l # set layer index
            layer_input = rbm.output # get output from RBM for the next layer
            # build learning function
            cost, updates = rbm.get_cost_updates()
            rbm.learn = theano.function(
                [self.input,
                 theano.In(rbm.lr, value=0.1),
                 theano.In(rbm.fr, value=0.),
                 theano.In(rbm.orth_on, value=False)],
                cost, updates = updates, name = 'RBM.learn')
            self.rbms.append(rbm)
    # pretrain RBMs
    def pretrain(self, data_source, epochs = 7, lrs=[], frs=[]):
        assert type(data_source) is Server, 'Input data_source must be a Server.'
        assert data_source.Nv==self.Nls[0], 'Sample size %d does not fit the visible layer size %d'%(data_source.Nv, self.Nls[0])
        # layer-wise pretrain
        for rbm in self.rbms:
            print('Pretraining layer %d:'%rbm.layer)
            cost_avg0 = 10000.
            # go through pretraining epoches
            for epoch in range(epochs):
                # get learning rate and forgetting rate
                try: # learning rate
                    lr = lrs[epoch]
                except: # default 0.1
                    lr = 0.1
                try: # forgetting rate
                    fr = frs[epoch]
                except: # default 0.
                    fr = 0.
                # go through training set served by data_source
                costs = []
                for batch in data_source:
                    cost = rbm.learn(batch, lr, fr, epoch == 0)
                    costs.append(numpy.asscalar(cost))
                # calculate cost average and standard deviation
                cost_avg = numpy.mean(costs)
                print('    Epoch %d: cost = %f'%(epoch,cost_avg/numpy.log(2)))
                # if effectively no progress
                if abs(cost_avg0-cost_avg)<0.001: 
                    break # quit next epoch
                else: # otherwise save cost_avg and start next epoch
                    cost_avg0 = cost_avg
''' Server (data server)
dataset::numpy matrix
'''
class Server(object):
    # build data server
    def __init__(self, dataset, batch_size = 20):
        # initialize dataset
        self.dataset = numpy.asarray(dataset,dtype=theano.config.floatX)
        self.data_size, self.Nv = self.dataset.shape
        self.batch_size = batch_size
        self.batch = numpy.empty(shape=(self.batch_size, self.Nv),
                dtype=theano.config.floatX)
        self.batch_top = 0
        self.data_top = 0
    # define iterator 
    def __iter__(self):
        return self
    def __next__(self):
        # if dataset not exhausted
        if self.data_top < self.data_size:
            # get current capacity and data load
            batch_capacity = self.batch_size - self.batch_top
            data_load = self.data_size - self.data_top
            # if fewer data to fill up the batch
            if data_load < batch_capacity:
                # dump rest of the data
                self.batch[self.batch_top:self.batch_top+data_load,:] = self.dataset[self.data_top:,:]
                # update batch top
                self.batch_top += data_load
                self.data_top = 0 # rewind to the begining
                # will not yeild new batch
                raise StopIteration
            else: # more data left to fill batch
                # fill up the batch
                self.batch[self.batch_top:,:] = self.dataset[self.data_top:self.data_top+batch_capacity,:]
                # reset batch top
                self.batch_top = 0
                self.data_top += batch_capacity
                # yeild the filled batch
                return self.batch
        else: # dataset exhausted -> restart
            self.data_top = 0 # rewind to the begining
            # will not yeild new batch
            raise StopIteration
    # add data to the dataset
    def add_data(self, dataset):
        # update dataset
        self.dataset = numpy.append(self.dataset, dataset, axis = 0)
        self.data_size, self.Nv = self.dataset.shape

