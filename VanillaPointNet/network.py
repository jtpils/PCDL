import tensorflow as tf
import re
import tf_utils


def log_str(message):
    print message
    with open('log.log','a') as f:
        f.write(message)

def inference(input,is_training,num_classes,input_dim,bn_decay):
    mlp1=tf_utils.conv2d(input,64,[1,input_dim],'mlp1',[1,1],'VALID',bn=True,is_training=is_training,bn_decay=bn_decay)
    mlp2=tf_utils.conv2d(mlp1,64,[1,1],'mlp2',[1,1],'VALID',bn=True,is_training=is_training,bn_decay=bn_decay)
    mlp3=tf_utils.conv2d(mlp2,64,[1,1],'mlp3',[1,1],'VALID',bn=True,is_training=is_training,bn_decay=bn_decay)
    mlp4=tf_utils.conv2d(mlp3,128,[1,1],'mlp4',[1,1],'VALID',bn=True,is_training=is_training,bn_decay=bn_decay)
    mlp5=tf_utils.conv2d(mlp4,1024,[1,1],'mlp5',[1,1],'VALID',bn=True,is_training=is_training,bn_decay=bn_decay)

    with tf.name_scope('global_pool'):
        feature_pool=tf.reduce_max(mlp5,axis=1,name='pooling')
        feature_pool=tf.reshape(feature_pool,[-1,1024])
        feature_pool =tf.cond(is_training,
            lambda: tf.nn.dropout(feature_pool,0.7),
            lambda: feature_pool)

    fc1=tf_utils.fully_connected(feature_pool,512,'fc1',bn=True,bn_decay=bn_decay,is_training=is_training)
    fc2=tf_utils.fully_connected(fc1,256,'fc2',bn=True,bn_decay=bn_decay,is_training=is_training)
    fc3=tf_utils.fully_connected(fc2,num_classes,'fc3',bn=True,bn_decay=bn_decay,is_training=is_training)

    return fc3


def loss(logits,labels):
    labels=tf.cast(labels,tf.int64)
    cross_entropy=tf.nn.sparse_softmax_cross_entropy_with_logits(
        labels=labels,logits=logits,name='cross_entropy_per_example')
    cross_entropy_mean=tf.reduce_sum(cross_entropy,name='cross_entropy')
    tf.add_to_collection('losses',cross_entropy_mean)

    return tf.add_n(tf.get_collection('losses'),name='total_loss')


def tower_loss(scope, pcs, labels, is_training, num_classes, input_dim, bn_decay):
    """Calculate the total loss on a single tower running the CIFAR model.
    Args:
    scope: unique prefix string identifying the CIFAR tower, e.g. 'tower_0'
    images: Images. 4D tensor of shape [batch_size, height, width, 3].
    labels: Labels. 1D tensor of shape [batch_size].
    Returns:
     Tensor of shape [] containing the total loss for a batch of data
    """

    # Build inference Graph.
    logits = inference(pcs, is_training, num_classes,input_dim, bn_decay)

    # Build the portion of the Graph calculating the losses. Note that we will
    # assemble the total_loss using a custom function below.
    _ = loss(logits, labels)

    # Assemble all of the losses for the current tower only.
    losses = tf.get_collection('losses', scope)

    # Calculate the total loss for the current tower.
    total_loss = tf.add_n(losses, name='total_loss')

    # Attach a scalar summary to all individual losses and the total loss; do the
    # same for the averaged version of the losses.
    for l in losses + [total_loss]:
        # Remove 'tower_[0-9]/' from the name in case this is a multi-GPU training
        # session. This helps the clarity of presentation on tensorboard.
        loss_name = re.sub('tower_[0-9]*/', '', l.op.name)
        tf.summary.scalar(loss_name, l)

    return total_loss,logits


def average_gradients(tower_grads):
    """Calculate the average gradient for each shared variable across all towers.
    Note that this function provides a synchronization point across all towers.
    Args:
    tower_grads: List of lists of (gradient, variable) tuples. The outer list
      is over individual gradients. The inner list is over the gradient
      calculation for each tower.
    Returns:
     List of pairs of (gradient, variable) where the gradient has been averaged
     across all towers.
    """
    average_grads = []
    for grad_and_vars in zip(*tower_grads):
        # Note that each grad_and_vars looks like the following:
        #   ((grad0_gpu0, var0_gpu0), ... , (grad0_gpuN, var0_gpuN))
        grads = []
        for g, _ in grad_and_vars:
            # Add 0 dimension to the gradients to represent the tower.
            expanded_g = tf.expand_dims(g, 0)

            # Append on a 'tower' dimension which we will average over below.
            grads.append(expanded_g)

        # Average over the 'tower' dimension.
        grad = tf.concat(axis=0, values=grads)
        grad = tf.reduce_mean(grad, 0)

        # Keep in mind that the Variables are redundant because they are shared
        # across towers. So .. we will just return the first tower's pointer to
        # the Variable.
        v = grad_and_vars[0][1]
        grad_and_var = (grad, v)
        average_grads.append(grad_and_var)

    return average_grads


def get_bn_decay(batch,batch_size):
    bn_momentum = tf.train.exponential_decay(
                      0.5,
                      batch*batch_size,
                      20000,
                      0.8,
                      staircase=True)
    bn_decay = tf.minimum(0.99, 1 - bn_momentum)
    return bn_decay


def train_op(inputs,labels,batch_size,is_training,num_gpu,initial_lr,lr_decay_rate,num_batches_per_epoch,decay_epoch,
             num_classes,input_dim,momentum=0.9):

    global_step=tf.get_variable('global_step',shape=[],
                                initializer=tf.constant_initializer(0),
                                trainable=False)

    bn_decay=get_bn_decay(global_step,batch_size)

    decay_steps=int(num_batches_per_epoch*decay_epoch)
    lr=tf.train.exponential_decay(initial_lr,global_step,decay_steps,lr_decay_rate,True)
    # opt=tf.train.MomentumOptimizer(lr,momentum=momentum)
    opt=tf.train.GradientDescentOptimizer(lr)

    tower_grads=[]
    tower_logits=[]
    tower_losses=[]
    for i in xrange(num_gpu):
        with tf.device('/gpu:{}'.format(i)):
            with tf.name_scope('%s_%d'%('tower',i)) as scope:

                if i==0:
                    with tf.variable_scope(tf.get_variable_scope(),reuse=False):
                        loss_val,logits=tower_loss(scope,inputs[i],labels[i], is_training,num_classes,input_dim,bn_decay)
                else:
                    with tf.variable_scope(tf.get_variable_scope(),reuse=True):
                        loss_val,logits=tower_loss(scope,inputs[i],labels[i], is_training,num_classes,input_dim,bn_decay)

                grads=opt.compute_gradients(loss_val)
                tower_losses.append(loss_val)
                tower_grads.append(grads)
                tower_logits.append(logits)

    grads=average_gradients(tower_grads)
    loss_val=tf.reduce_mean(tf.concat(tower_losses,axis=0))

    summaries = tf.get_collection(tf.GraphKeys.SUMMARIES)
    summaries.append(tf.summary.scalar('learning_rate',lr))
    summaries.append(tf.summary.scalar('loss_val',loss_val))

    apply_gradient_op=opt.apply_gradients(grads,global_step=global_step)

    for var in tf.trainable_variables():
        summaries.append(tf.summary.histogram(var.op.name,var))

    summaries_op=tf.summary.merge(summaries)

    return apply_gradient_op,summaries_op,tower_logits,loss_val


from preprocess import ModelBatchReader,normalize,add_noise
import PointSample
import time
def train():
    batch_files=['data/ModelNet40/train0.batch',
                 'data/ModelNet40/train1.batch',
                 'data/ModelNet40/train2.batch',
                 'data/ModelNet40/train3.batch']
    batch_size=30
    thread_num=2
    gpu_num=2

    initial_lr=1e-1
    init_stddev=1e-3
    lr_decay=0.85
    decay_epoch=5
    num_classes=40
    input_dim=3
    pt_num=2048
    train_epoch=100
    train_noise_stddev=1e-2
    model_name='model/model.ckpt-16236'

    show_info_step=30

    def test_aug(pcs):
        return normalize(pcs)

    def train_aug(pcs):
        pcs=normalize(pcs)
        pcs=add_noise(pcs,train_noise_stddev)
        return pcs

    reader=ModelBatchReader(batch_files,batch_size,thread_num)

    test_batch_files=['data/ModelNet40/test0.batch']
    test_reader=ModelBatchReader(test_batch_files,batch_size,thread_num,model='test')

    num_batches_per_epoch=int(reader.total_size/float(gpu_num*batch_size))
    train_steps=train_epoch*num_batches_per_epoch

    with tf.device('/cpu:0'):

        inputs = []
        labels = []
        is_training=tf.placeholder(tf.bool)
        for k in xrange(gpu_num):
            inputs.append(tf.placeholder(tf.float32,shape=[None,None,input_dim,1],name='input_placeholder_{}'.format(k)))
            labels.append(tf.placeholder(tf.int64,shape=[None,],name='label_placeholder_{}'.format(k)))

        apply_gradient_op,summaries_op,tower_logits,loss =train_op(inputs,labels,batch_size,is_training,gpu_num,initial_lr,
                                                                  lr_decay,num_batches_per_epoch,decay_epoch,
                                                                  num_classes,input_dim)
        summary_writer = tf.summary.FileWriter('train',tf.get_default_graph())

    config=tf.ConfigProto()
    config.gpu_options.allow_growth=True
    config.allow_soft_placement=True
    sess=tf.Session(graph=tf.get_default_graph(),config=config)
    sess.run(tf.global_variables_initializer())

    saver = tf.train.Saver(tf.global_variables())
    # saver.restore(sess,model_name)

    begin_time=time.time()
    cost_time=0
    for step in xrange(train_steps):
        feed_dict={}
        for k in xrange(gpu_num):
            data,label=reader.get_batch(pt_num,3,PointSample.getRotatedPointCloud,train_aug)

            feed_dict[inputs[k]]=data
            feed_dict[labels[k]]=label

        feed_dict[is_training]=True
        _,loss_val=sess.run([apply_gradient_op,loss],feed_dict)

        if step%show_info_step==0:
            cost_time+=time.time()-begin_time
            log_str('loss_val:{} speed: {} examples per second'.format(loss_val/batch_size,show_info_step*2.0*batch_size/cost_time))
            cost_time=0
            begin_time=time.time()

            summary_str=sess.run(summaries_op,feed_dict=feed_dict)
            summary_writer.add_summary(summary_str,global_step=step)

        if step%num_batches_per_epoch==0:
            print 'predicting...'
            # test model
            correct_num=0
            while True:
                break_flag=False
                for k in xrange(gpu_num):
                    data,label=test_reader.get_batch(pt_num,3,PointSample.getPointCloud,test_aug)

                    if data is None:
                        break_flag=True
                        break

                    feed_dict[is_training]=False
                    feed_dict[inputs[k]]=data
                    feed_dict[labels[k]]=label

                if break_flag:
                    break

                logits=sess.run(tower_logits,feed_dict)
                for k,l in enumerate(logits):
                    correct_num+=np.sum(np.argmax(l,axis=1)==feed_dict[labels[k]])

            # print 'predicting finished'

            log_str('epoch {0} test acc:{1}'.format(step/num_batches_per_epoch,correct_num/float(test_reader.total_size)))

            #save
            saver.save(sess,'model/model.ckpt',global_step=step)



###########test code below###########
import numpy as np
def test_inference_loss():

    # test inference and loss
    input_dim=3
    with tf.device('/cpu:0'):
        input=tf.placeholder(dtype=tf.float32,shape=[None,None,input_dim,1],name='input')
        labels=tf.placeholder(dtype=tf.int64,shape=[None,],name='labels')
        logits=inference(input,40,input_dim)
        loss_val=loss(logits,labels)

    sess=tf.Session(graph=tf.get_default_graph())

    sess.run(tf.global_variables_initializer())

    total_loss=0
    correct_num=0
    for _ in xrange(300):
        true_labels=np.random.random_integers(0,39,10)
        predicts,losses=sess.run([logits,loss_val],
                                 feed_dict={input:np.random.uniform(-1,1,[10,4096,input_dim,1]),
                                            labels:true_labels})
        correct_num+=np.sum((np.argmax(predicts,axis=1)==true_labels))
        total_loss+=losses

    # sanity check 1
    print 'correct_num:{0} total_losses:{1}'.format(correct_num/3000.0,total_loss/3000.0)
    import math
    print math.log(1.0/40.0)

def test_over_fit_ability():
    # batch_files = ['data/ModelNet40/train0.batch',
    #                'data/ModelNet40/train1.batch',
    #                'data/ModelNet40/train2.batch',
    #                'data/ModelNet40/train3.batch']
    batch_size = 30
    thread_num = 4
    gpu_num = 2

    initial_lr = 1e-3
    lr_decay = 0.9
    decay_epoch = 5
    num_classes = 40
    input_dim = 3
    pt_num = 2096
    train_epoch = 100

    # reader = ModelBatchReader(batch_files, batch_size, thread_num)

    # test_batch_files = ['data/ModelNet40/test0.batch']
    # test_reader = ModelBatchReader(test_batch_files, batch_size, thread_num)

    num_batches_per_epoch = 500
    train_steps = train_epoch * num_batches_per_epoch


    with tf.device('/cpu:0'):

        inputs = []
        labels = []
        for k in xrange(gpu_num):
            inputs.append(
                tf.placeholder(tf.float32, shape=[None, None, input_dim, 1], name='input_placeholder_{}'.format(k)))
            labels.append(tf.placeholder(tf.int64, shape=[None, ], name='label_placeholder_{}'.format(k)))

        is_training=tf.placeholder(tf.bool)
        apply_gradient_op,summaries_op,tower_logits,loss =train_op(inputs,labels,batch_size,is_training,gpu_num,initial_lr,
                                                                  lr_decay,num_batches_per_epoch,decay_epoch,
                                                                  num_classes,input_dim)
        summary_writer = tf.summary.FileWriter('train', tf.get_default_graph())

    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True
    config.allow_soft_placement = True
    sess = tf.Session(graph=tf.get_default_graph(), config=config)
    sess.run(tf.global_variables_initializer())

    saver = tf.train.Saver(tf.global_variables())

    fixed_data = np.random.uniform(-1, 1, [batch_size, pt_num, input_dim, 1])
    fixed_label = np.random.random_integers(0, 39, [batch_size, ])

    begin_time = time.time()
    cost_time = 0
    for step in xrange(train_steps):
        feed_dict = {}
        for k in xrange(gpu_num):
            # data,label=reader.get_batch(pt_num,3,PointSample.getPointCloud)

            data = fixed_data
            label = fixed_label

            feed_dict[inputs[k]] = data
            feed_dict[labels[k]] = label

        feed_dict[is_training] = True

        _, loss_val = sess.run([apply_gradient_op, loss], feed_dict)

        if step % 100 == 0:
            cost_time += time.time() - begin_time
            print 'loss_val:{} speed: {} examples per second'.format(loss_val / batch_size,
                                                                     100.0 * batch_size / cost_time)
            cost_time = 0
            begin_time = time.time()

            summary_str = sess.run(summaries_op, feed_dict=feed_dict)
            summary_writer.add_summary(summary_str, global_step=step)

        if step % num_batches_per_epoch == 0:
            print 'predicting...'
            # test model
            correct_num = 0
            while True:
                break_flag = False
                for k in xrange(gpu_num):
                    # data,label=test_batch_files.get_batch(pt_num,3,PointSample.getPointCloud)
                    data = fixed_data
                    label = fixed_label

                    if data is None:
                        break_flag = True
                        break
                    feed_dict[inputs[k]] = data
                    feed_dict[labels[k]] = label
                    feed_dict[is_training] = False

                if break_flag:
                    break

                logits = sess.run(tower_logits, feed_dict)
                for k, l in enumerate(logits):
                    correct_num += np.sum(np.argmax(l, axis=1) == feed_dict[labels[k]])
                    print np.argmax(l, axis=1)
                    print feed_dict[labels[k]]

                break

            print 'predicting finished'

            # print 'test acc:{}'.format(correct_num/float(test_reader.total_size))
            print 'test acc:{}'.format(correct_num / float(batch_size * 2))

            # save
            saver.save(sess, 'model/model.ckpt', global_step=step)

if __name__=="__main__":
    test_over_fit_ability()


